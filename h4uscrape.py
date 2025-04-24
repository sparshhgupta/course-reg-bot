import json
import time
import os
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import re
import traceback

# Create a debug directory
debug_dir = "debug_output"
os.makedirs(debug_dir, exist_ok=True)

# URL of the website
base_url = "https://handoutsforyou.vercel.app"
reviews_url = f"{base_url}/courses/reviews"

# Setup Chrome options
chrome_options = Options()
chrome_options.add_argument("--window-size=1920,1080")
# Uncomment the line below to run Chrome in headless mode (no UI)
# chrome_options.add_argument("--headless")

def setup_driver():
    """Initialize and return the WebDriver"""
    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        return driver
    except Exception as e:
        print(f"Error setting up WebDriver: {e}")
        raise

# Initialize the WebDriver
try:
    driver = setup_driver()
except Exception as e:
    print(f"Failed to initialize WebDriver: {e}")
    exit(1)

def save_page_source(filename, custom_message=None):
    """Save the current page source to a file for debugging"""
    html = driver.page_source
    filepath = os.path.join(debug_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        if custom_message:
            f.write(f"<!-- {custom_message} -->\n")
        f.write(html)
    print(f"Saved page source to {filepath}")
    
    # Also save a screenshot
    screenshot_path = os.path.join(debug_dir, f"{filename.replace('.html', '')}.png")
    driver.save_screenshot(screenshot_path)
    print(f"Saved screenshot to {screenshot_path}")
    
    return html

def wait_for_element(by, selector, timeout=10, condition="clickable"):
    """Wait for an element to be present/visible/clickable"""
    try:
        if condition == "clickable":
            element = WebDriverWait(driver, timeout).until(
                EC.element_to_be_clickable((by, selector))
            )
        elif condition == "visible":
            element = WebDriverWait(driver, timeout).until(
                EC.visibility_of_element_located((by, selector))
            )
        elif condition == "present":
            element = WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((by, selector))
            )
        return element
    except TimeoutException:
        print(f"Timeout waiting for element: {selector}")
        save_page_source(f"timeout_{selector.replace(' ', '_')}.html")
        return None

def parse_reviews_from_html(html_content):
    """Parse reviews from HTML using BeautifulSoup with improved detection"""
    soup = BeautifulSoup(html_content, 'html.parser')
    reviews = []
    
    # Try to identify the course name and professor
    course_name = "Unknown Course"
    course_heading = soup.find(['h1', 'h2', 'h3'], string=lambda s: s and "CS F" in s)
    if course_heading:
        course_name = course_heading.text.strip()
    
    # Extract professor name
    professor = "Unknown"
    professor_element = soup.find(string=lambda text: text and "Professor:" in text)
    if professor_element:
        prof_match = re.search(r"Professor:\s*(.*?)(?:\s*\||$)", professor_element)
        if prof_match:
            professor = prof_match.group(1).strip()
    
    # Look for review elements more systematically
    review_elements = []
    
    # Approach 1: Look for elements that might be review containers
    potential_containers = soup.find_all(['div', 'article', 'section'], class_=lambda c: c and any(
        term in c.lower() for term in ['review', 'comment', 'feedback']
    ))
    
    # Approach 2: Look for elements with review-like structure 
    # (e.g., a div containing reviewer name, rating, and comment)
    all_divs = soup.find_all('div')
    for div in all_divs:
        text = div.text.strip()
        # Skip very small divs or those without substantial text
        if len(text) < 20:
            continue
            
        # Check if this div might contain a complete review
        if len(div.find_all(['p', 'span', 'div'])) >= 2:
            potential_containers.append(div)
    
    # If we found specific containers, use them
    if potential_containers:
        review_elements = potential_containers
    else:
        # Otherwise, use a fallback approach: try to identify reviews from the page structure
        # In this case we'll consider all divs with sufficient content as potential reviews
        review_elements = [div for div in all_divs if len(div.text.strip()) > 50]
    
    # Map of already processed elements to avoid duplicates
    processed = set()
    
    # Process each potential review element
    for element in review_elements:
        # Skip if we've already processed this element or its parents
        if any(id(element) == p_id or element.parent and id(element.parent) == p_id for p_id in processed):
            continue
        
        # Add to processed set
        processed.add(id(element))
        
        # Extract review information
        review_info = extract_review_info(element)
        if review_info:
            review_info["professor"] = professor
            reviews.append(review_info)
    
    # If no reviews were found using the approaches above, try a more aggressive approach
    if not reviews:
        # Try to find any text that looks like a review
        all_paragraphs = soup.find_all(['p', 'div', 'span'])
        for p in all_paragraphs:
            text = p.text.strip()
            # Skip short text
            if len(text) < 30:
                continue
                
            # Skip if parent was already processed
            if any(p.parent and id(p.parent) == p_id for p_id in processed):
                continue
                
            # Check if this looks like a review
            if any(term in text.lower() for term in ['experience', 'class', 'professor', 'course', 'learned', 'recommend']):
                processed.add(id(p))
                reviews.append({
                    "reviewer": "Anonymous",
                    "rating": "N/A",
                    "comment": text,
                    "professor": professor
                })
    
    return reviews

def extract_review_info(element):
    """Extract reviewer, rating, and comment from a review element"""
    text = element.text.strip()
    
    # Skip elements that are too short to be reviews
    if len(text) < 20:
        return None
    
    # Default values
    reviewer = "Anonymous"
    rating = "N/A"
    comment = text
    
    # Try to identify the reviewer
    # Look for patterns like "Posted by [name]", "Review by [name]", etc.
    reviewer_patterns = [
        r"(?:posted|reviewed|submitted|written) by:?\s*([^,\n]+)",
        r"(?:user|reviewer|student):?\s*([^,\n]+)",
        r"([^:]+)(?:rated|reviewed|commented)"
    ]
    
    for pattern in reviewer_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            reviewer = match.group(1).strip()
            break
    
    # Try to identify rating
    # Look for patterns like "4/5", "3 stars", "Rating: 8/10", etc.
    rating_patterns = [
        r"(\d+(?:\.\d+)?)\s*(?:out of|\/)\s*(\d+)(?:\s*stars?)?",
        r"rating:?\s*(\d+(?:\.\d+)?)\s*(?:out of|\/)\s*(\d+)",
        r"(\d+(?:\.\d+)?)\s*stars?"
    ]
    
    for pattern in rating_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            if len(match.groups()) == 2:
                rating = f"{match.group(1)}/{match.group(2)}"
            else:
                rating = f"{match.group(1)}/5"  # Assuming 5-star scale if not specified
            break
    
    # Try to extract comment - look for the main body of text
    # First, check if there are any paragraphs inside
    paragraphs = element.find_all('p')
    if paragraphs:
        # Find the longest paragraph, which is likely the main comment
        longest_p = max(paragraphs, key=lambda p: len(p.text.strip()))
        comment = longest_p.text.strip()
    else:
        # If no paragraphs, try to extract comment by removing reviewer and rating info
        comment = text
        if reviewer != "Anonymous":
            for pattern in reviewer_patterns:
                comment = re.sub(pattern, '', comment, flags=re.IGNORECASE).strip()
        
        if rating != "N/A":
            for pattern in rating_patterns:
                comment = re.sub(pattern, '', comment, flags=re.IGNORECASE).strip()
    
    return {
        "reviewer": reviewer,
        "rating": rating,
        "comment": comment
    }

def fetch_courses():
    """Fetch course list from dropdown or ask for manual input"""
    all_courses = []
    
    # Wait for page to load properly
    time.sleep(2)
    
    # Try to find the dropdown/search input
    search_input = wait_for_element(By.CSS_SELECTOR, 
                                   "input[placeholder*='Search'], .dropdown input, input[type='text']", 
                                   timeout=10)
    
    if search_input:
        try:
            # Click to open dropdown
            search_input.click()
            time.sleep(1)
            
            # Look for course elements
            course_elements = WebDriverWait(driver, 5).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, 
                                                   ".dropdown-content li, .dropdown-menu li, .autocomplete-results li"))
            )
            
            # Extract course names
            all_courses = [course.text.strip() for course in course_elements if course.text.strip()]
            print(f"Found {len(all_courses)} courses in dropdown.")
            
            # Close any open dropdown by clicking elsewhere
            driver.find_element(By.TAG_NAME, "body").click()
            time.sleep(0.5)
            
        except Exception as e:
            print(f"Error getting course list from dropdown: {e}")
            save_page_source("dropdown_error.html")
    
    # If no courses found automatically, ask for manual input
    if not all_courses:
        print("\nPlease enter the course codes to scrape, separated by commas:")
        print("Example: AN F311, BIO F110, CS F111")
        courses_input = input("Enter courses: ")
        all_courses = [course.strip() for course in courses_input.split(",")]
    
    return all_courses

def get_reviews_for_course(course_name):
    """Get reviews for a specific course with improved error handling"""
    try:
        print(f"\nGetting reviews for: {course_name}")
        
        # Look for search input with multiple selectors to improve reliability
        search_input = wait_for_element(By.CSS_SELECTOR, 
                                      "input[placeholder*='Search'], .dropdown input, input[type='text']", 
                                      timeout=10)
        
        if not search_input:
            print("Could not find search input. Saving debug info.")
            save_page_source(f"search_input_not_found_{course_name.replace(' ', '_')}.html")
            return []
        
        # Clear existing text and enter the course code
        search_input.clear()
        search_input.send_keys(course_name)
        time.sleep(1.5)  # Allow dropdown to populate
        
        # Try multiple approaches to select the course
        course_selected = False
        
        # Approach 1: Click on the course in dropdown results
        try:
            # Try various selectors for the dropdown items
            selectors = [
                f"//button[contains(text(), '{course_name}')]",
                f"//li[contains(text(), '{course_name}')]",
                f"//div[contains(@class, 'dropdown-item') and contains(text(), '{course_name}')]",
                f"//div[contains(text(), '{course_name}')]"
            ]
            
            for selector in selectors:
                try:
                    course_option = wait_for_element(By.XPATH, selector, timeout=3)
                    if course_option:
                        course_option.click()
                        course_selected = True
                        print(f"Selected course '{course_name}' from dropdown.")
                        time.sleep(1)
                        break
                except:
                    continue
        except Exception as e:
            print(f"Error selecting course from dropdown: {e}")
        
        # Approach 2: If dropdown selection fails, try pressing Enter key
        if not course_selected:
            try:
                from selenium.webdriver.common.keys import Keys
                search_input.send_keys(Keys.RETURN)
                time.sleep(1)
                course_selected = True
                print(f"Submitted course '{course_name}' using Enter key.")
            except Exception as e:
                print(f"Error submitting course with Enter key: {e}")
        
        # Approach 3: Look for a confirmation that the course is selected
        if not course_selected:
            try:
                course_display = wait_for_element(By.XPATH, 
                                                f"//div[contains(text(), '{course_name}')]", 
                                                timeout=3,
                                                condition="visible")
                if course_display:
                    course_selected = True
                    print(f"Course '{course_name}' appears to be selected.")
            except:
                pass
        
        # Find and click "Fetch Reviews" button with multiple selector options
        fetch_button = None
        button_selectors = [
            "//button[contains(text(), 'Fetch Reviews')]",
            "//button[contains(text(), 'Get Reviews')]",
            "//button[contains(@class, 'fetch')]",
            "//button[contains(@class, 'submit')]",
            "//button[contains(@type, 'submit')]"
        ]
        
        for selector in button_selectors:
            try:
                fetch_button = wait_for_element(By.XPATH, selector, timeout=3)
                if fetch_button:
                    break
            except:
                continue
        
        if fetch_button:
            fetch_button.click()
            print("Clicked 'Fetch Reviews' button.")
            time.sleep(3)  # Wait for reviews to load
        else:
            print("Could not find 'Fetch Reviews' button. Saving debug info.")
            save_page_source(f"fetch_button_not_found_{course_name.replace(' ', '_')}.html")
            return []
        
        # Save debug info of the reviews page
        html_content = save_page_source(f"reviews_{course_name.replace(' ', '_')}.html",
                                       f"Reviews page for {course_name}")
        
        # Extract the reviews
        reviews = parse_reviews_from_html(html_content)
        
        if not reviews:
            print(f"No reviews found for {course_name} or could not parse reviews.")
        else:
            print(f"Successfully extracted {len(reviews)} reviews for {course_name}.")
        
        return reviews
            
    except Exception as e:
        print(f"Error getting reviews for {course_name}: {e}")
        traceback.print_exc()
        save_page_source(f"error_{course_name.replace(' ', '_')}.html")
        return []

course_codes = [
    "CS F111",
    "CS F211",
    "CS F212",
    "CS F213",
    "CS F241",
    "CS F266",
    "CS F303",
    "CS F342",
    "CS F363",
    "CS F364",
    "CS F366",
    "CS F367",
    "CS F372",
    "CS F376",
    "CS F377",
    "CS F407",
    "CS F415",
    "CS F433",
    "CS F436",
    "CS F437",
    "CS F469",
    "CS F491",
    "CS G513",
    "CS G520",
    "CS G523",
    "CS G524",
    "CS G527"
]

def main():
    try:
        # First navigate to reviews page
        driver.get(reviews_url)
        print("Navigating to reviews page.")
        
        # Allow user to manually log in with Google
        print("\n" + "="*50)
        print("MANUAL LOGIN REQUIRED")
        print("="*50)
        print("1. Please log in with your Google account in the browser window")
        print("2. After successful login, navigate to the reviews page if not automatically redirected")
        print("3. Once you're on the reviews page, type 'done' and press Enter here")
        print("="*50)
        
        user_input = ""
        while user_input.lower() != "done":
            user_input = input("Type 'done' when logged in and on the reviews page: ")
        
        # Ensure we're on the reviews page
        current_url = driver.current_url
        if "/courses/reviews" not in current_url:
            print(f"Navigating to reviews page from {current_url}")
            driver.get(reviews_url)
            time.sleep(3)
        
        print("Great! Now starting to scrape reviews...")
        
        # Get list of courses to scrape
        # all_courses = fetch_courses()
        all_courses = course_codes
        
        if not all_courses:
            print("No courses found or specified. Exiting.")
            return
        
        # Dictionary to store reviews for each course
        all_reviews = {}
        
        # Process each course
        for i, course in enumerate(all_courses):
            print(f"\nProcessing course {i+1}/{len(all_courses)}: {course}")
            course_reviews = get_reviews_for_course(course)
            
            # Store reviews in the dictionary
            all_reviews[course] = course_reviews
            print(f"Found {len(course_reviews)} reviews for {course}")
            
            # Save after each course in case of interruption
            with open("course_reviews_partial.json", "w", encoding="utf-8") as f:
                json.dump(all_reviews, f, ensure_ascii=False, indent=4)
            
            # Sleep briefly to avoid overwhelming the server
            time.sleep(1)
        
        # Save final results to JSON file
        with open("course_reviews.json", "w", encoding="utf-8") as f:
            json.dump(all_reviews, f, ensure_ascii=False, indent=4)
        
        print("\nAll reviews have been saved to course_reviews.json")
        print(f"Debug files are available in the '{debug_dir}' directory")
        
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        traceback.print_exc()
        save_page_source("fatal_error.html")
    
    finally:
        print("\nDo you want to close the browser? (yes/no)")
        close_browser = input("Enter your choice: ").lower()
        if close_browser == "yes":
            driver.quit()
            print("Browser closed.")
        else:
            print("Browser left open. You need to close it manually when finished.")

if __name__ == "__main__":
    main()