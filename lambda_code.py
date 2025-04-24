import json
import urllib.request
import re
import boto3
import uuid
import itertools
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
import os

# Initialize DynamoDB client
dynamodb = boto3.resource('dynamodb')
user_table = dynamodb.Table('LexBotUserData')

def normalize(text):
    if not text:
        return ""
    return re.sub(r'[^a-z0-9]', '', text.lower())

def fetch_course_data():
    url = os.environ['COURSE_DETAILS']
    response = urllib.request.urlopen(url)
    return json.loads(response.read())

def fetch_professor_reviews():
    url = os.environ['PROF_DETAILS']
    response = urllib.request.urlopen(url)
    return json.loads(response.read())

def get_user_data(user_id):
    """Retrieve user data from DynamoDB"""
    try:
        response = user_table.get_item(Key={'userid': user_id})
        return response.get('Item', {})
    except ClientError as e:
        print(f"Error retrieving user data: {e.response['Error']['Message']}")
        return {}

def update_user_data(user_id, updates):
    """Update user data in DynamoDB"""
    if not updates:
        print("No updates to save to DynamoDB")
        return
        
    try:
        # Check if user exists
        exists = False
        try:
            response = user_table.get_item(Key={'userid': user_id})
            exists = 'Item' in response
        except ClientError:
            exists = False
            
        if not exists:
            # Create new item if user doesn't exist
            print(f"Creating new user record for {user_id}")
            item = {'userid': user_id}
            item.update(updates)
            user_table.put_item(Item=item)
            return
                
        # Prepare update expression and attribute values
        update_expression = "SET "
        expression_attribute_values = {}
        expression_attribute_names = {}
        
        for key, value in updates.items():
            update_expression += f"#{key} = :{key}, "
            expression_attribute_values[f":{key}"] = value
            expression_attribute_names[f"#{key}"] = key
        
        # Remove trailing comma and space
        update_expression = update_expression[:-2]
        
        # Update the item
        print(f"Updating DynamoDB for user {user_id} with expression: {update_expression}")
        print(f"Attribute values: {expression_attribute_values}")
        
        user_table.update_item(
            Key={'userid': user_id},
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expression_attribute_names,
            ExpressionAttributeValues=expression_attribute_values
        )
        print("DynamoDB update successful")
    except ClientError as e:
        print(f"Error updating user data: {e.response['Error']['Message']}")
        print(f"Updates that failed: {updates}")

def generate_user_id(event):
    """Generate a consistent user ID from the event data"""
    # Try several sources for user identification
    user_id = None
    
    # Check for Lex-specific identifiers
    if 'sessionId' in event:
        user_id = event['sessionId']
    elif 'userId' in event:
        user_id = event['userId']
    elif 'requestAttributes' in event and 'x-amz-lex:user-id' in event['requestAttributes']:
        user_id = event['requestAttributes']['x-amz-lex:user-id']
    # Check for source phone number or other identifiers in session attributes
    elif 'sessionState' in event and 'sessionAttributes' in event['sessionState']:
        session_attrs = event['sessionState']['sessionAttributes'] or {}
        if 'phoneNumber' in session_attrs:
            user_id = session_attrs['phoneNumber']
        elif 'userId' in session_attrs:
            user_id = session_attrs['userId']
    
    # If no ID found, generate a UUID based on something from the event
    if not user_id:
        # Generate a consistent hash based on whatever identifying info we have
        hash_base = str(event.get('inputTranscript', '')) + str(event.get('invocationSource', ''))
        user_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, hash_base))
    
    print(f"Generated/extracted user ID: {user_id}")
    return user_id

def extract_current_course_code(event, user_data, slots):
    """Extract the current course code from various possible sources"""
    # First check if the current intent has a course slot filled
    if slots and 'courseIdentifier' in slots and slots['courseIdentifier']:
        slot_value = slots['courseIdentifier']
        if slot_value and 'value' in slot_value and 'interpretedValue' in slot_value['value']:
            print(f"Found course in slot: {slot_value['value']['interpretedValue']}")
            return slot_value['value']['interpretedValue']
    
    # If not in current slot, check if in inputTranscript
    if 'inputTranscript' in event:
        input_text = event['inputTranscript'].lower()
        # Extract course codes that match pattern like "CS F111" or "CSF111"
        matches = re.findall(r'[a-z]{2,4}\s*f?\s*\d{3}', input_text)
        if matches:
            print(f"Found course in input text: {matches[0].upper()}")
            return matches[0].upper()
    
    # Finally, check if in user data
    if 'lastCourseCode' in user_data:
        print(f"Found course in user data: {user_data['lastCourseCode']}")
        return user_data['lastCourseCode']
    
    return None

def handle_check_course_availability(event, user_data, slots):
    course_name = extract_current_course_code(event, user_data, slots)
    if not course_name:
        raise ValueError("Course name not provided.")
    
    course_query = normalize(course_name)
    data = fetch_course_data()

    found = None
    for course in data:
        code = normalize(course['course_code'])
        name = normalize(course['course_name'])
        if course_query == code or course_query in name:
            found = course
            break

    updates = {}
    if found:
        # Store the course code in the standard format
        updates['lastCourseCode'] = found['course_code']
        message = f"✅ Yes, the course '{found['course_name']}' ({found['course_code']}) is being offered. Would you like to know the instructor, credits, exam dates, or schedule?"
        
        # Store instructors for potential professor review queries
        instructors = list(set(sec.get('Instructor', 'Unknown') for sec in found['sections']))
        instructors = [i for i in instructors if i and i != 'Unknown']
        if instructors:
            updates['lastInstructors'] = instructors
            updates['lastProfessor'] = instructors[0]  # Default to first instructor
            
        # Store queried courses in user history
        courses_history = user_data.get('coursesHistory', [])
        if isinstance(courses_history, str):
            try:
                courses_history = json.loads(courses_history)
            except:
                courses_history = []
        
        if not isinstance(courses_history, list):
            courses_history = []
            
        if found['course_code'] not in courses_history:
            courses_history.append(found['course_code'])
            updates['coursesHistory'] = courses_history[:10]  # Keep last 10 courses
    else:
        message = f"❌ Sorry, '{course_name}' does not appear to be offered this semester."

    return message, updates

def handle_get_course_details(event, user_data, slots):
    # Get the input transcript to determine detail type if available
    input_text = event.get('inputTranscript', '').lower()
    
    # First check if it's a full detail request
    full_details = False
    if "full" in input_text or "all" in input_text or "everything" in input_text or "details" in input_text:
        full_details = True
    
    # Get course code from all possible sources
    course_code = extract_current_course_code(event, user_data, slots)
    
    # If still not found, raise error
    if not course_code:
        raise ValueError("No course selected previously. Please mention the course first.")

    print(f"Looking up details for course: {course_code}")
    
    # Fetch course data
    data = fetch_course_data()

    # Try to find exact match for course code
    found = None
    for course in data:
        if course['course_code'].lower() == course_code.lower():
            found = course
            break
    
    # If not found, try more flexible matching
    if not found:
        query = normalize(course_code)
        for course in data:
            code = normalize(course['course_code'])
            name = normalize(course['course_name'])
            if query == code or query in name:
                found = course
                break
    
    # If still not found, raise error
    if not found:
        raise ValueError(f"Course '{course_code}' not found in data.")

    # Initialize updates dict
    updates = {}
    updates['lastCourseCode'] = found['course_code']
    
    # Store instructors for potential professor review queries
    instructors = list(set(sec.get('Instructor', 'Unknown') for sec in found['sections'] if sec.get('Instructor')))
    instructors = [i for i in instructors if i and i != 'Unknown']
    if instructors:
        updates['lastInstructors'] = instructors
        updates['lastProfessor'] = instructors[0]  # Default to first instructor
    
    # Update course history
    courses_history = user_data.get('coursesHistory', [])
    if isinstance(courses_history, str):
        try:
            courses_history = json.loads(courses_history)
        except:
            courses_history = []
    
    if not isinstance(courses_history, list):
        courses_history = []
        
    if found['course_code'] not in courses_history:
        courses_history.append(found['course_code'])
        updates['coursesHistory'] = courses_history[:10]  # Keep last 10 courses

    # If full details requested, return complete information
    if full_details:
        details = f"📘 *{found['course_name']}* ({found['course_code']})\n"
        details += f"- L/P/U Credits: {found['L']}/{found['P']}/{found['U']}\n"
        details += f"- Lecture Sections: {found.get('lecture_sections', 0)}\n"
        details += f"- Tutorial Sections: {found.get('tut_sections', 0)}\n"
        details += f"- Practical Sections: {found.get('practical_sections', 0)}\n"
        details += f"- Midsem: {found.get('midsem', 'N/A')}\n"
        details += f"- Compre: {found.get('compre', 'N/A')}\n"
        details += f"- Instructor-in-Charge: {found.get('IC', 'N/A')}\n"
        details += f"- Sections:\n"
        for sec in found.get('sections', []):
            sec_name = sec.get('section_name', 'Unknown')
            instructor = sec.get('Instructor', 'Unknown')
            room = sec.get('Room', 'Unknown')
            sec_slots = ', '.join(sec.get('Days_Times', []))
            details += f"   - {sec_name}: {instructor} in {room} ({sec_slots})\n"
            
        return details, updates

    # Determine which specific detail the user is asking for
    detail_type = ""
    
    # First check if detail type is in slot
    if slots and 'courseDetailType' in slots and slots['courseDetailType'] and 'value' in slots['courseDetailType'] and 'interpretedValue' in slots['courseDetailType']['value']:
        detail_type = slots['courseDetailType']['value']['interpretedValue'].lower()
    # If not in slot, try to determine from input text
    else:
        if "instructor" in input_text or "professor" in input_text or "teacher" in input_text or "faculty" in input_text:
            detail_type = "instructor"
        elif "credit" in input_text or "unit" in input_text:
            detail_type = "credit"
        elif "midsem" in input_text or "mid semester" in input_text or "mid-sem" in input_text:
            detail_type = "midsem"
        elif "compre" in input_text or "comprehensive" in input_text or "final exam" in input_text:
            detail_type = "compre"
        elif "schedule" in input_text or "time" in input_text or "section" in input_text or "room" in input_text:
            detail_type = "schedule"

    print(f"Detail type requested: {detail_type}")
    
    # Generate response based on detail type
    if "instructor" in detail_type:
        instructors = list(set(sec.get('Instructor', 'Unknown') for sec in found['sections'] if sec.get('Instructor')))
        instructors = [i for i in instructors if i and i != 'Unknown']
        if not instructors:
            message = f"No instructor information available for {found['course_code']}."
        else:
            message = f"Instructors for {found['course_code']} are: " + ", ".join(instructors)
        
    elif "credit" in detail_type or "unit" in detail_type:
        message = f"{found['course_code']} has {found['L']} Lecture, {found['P']} Practical, {found['U']} Unit(s)."
    elif "midsem" in detail_type:
        message = f"Midsem for {found['course_code']} is scheduled on {found.get('midsem', 'Not available')}."
    elif "compre" in detail_type:
        message = f"Compre for {found['course_code']} is scheduled on {found.get('compre', 'Not available')}."
    elif "schedule" in detail_type or "section" in detail_type or "room" in detail_type:
        lines = []
        for sec in found.get('sections', []):
            sec_name = sec.get('section_name', 'Unknown')
            instructor = sec.get('Instructor', 'Unknown')
            room = sec.get('Room', 'Unknown')
            sec_slots = ', '.join(sec.get('Days_Times', []))
            lines.append(f"{sec_name} ({instructor}) in {room} at {sec_slots}")
        
        if lines:
            message = "📅 Schedule:\n" + "\n".join(lines)
        else:
            message = f"No schedule information available for {found['course_code']}."
    else:
        message = f"I can provide instructor, credits, exam dates or schedule. Please specify which detail you'd like."

    return message, updates

def handle_get_prof_reviews(event, user_data, slots):
    prof_slot = slots.get('profIdentifier')
    
    # Try to get professor name from slot
    if prof_slot and 'value' in prof_slot and 'interpretedValue' in prof_slot['value']:
        professor_name = prof_slot['value']['interpretedValue']
    # If not in slot, check if there's a last professor in user data
    elif 'lastProfessor' in user_data:
        professor_name = user_data['lastProfessor']
    else:
        raise ValueError("Professor name not provided. Please specify which professor you want reviews for.")
        
    # Normalize professor name for flexible matching
    prof_query = normalize(professor_name)
    
    # Get professor reviews data
    reviews_data = fetch_professor_reviews()
    
    # Find professor in reviews data
    found_prof = None
    for prof in reviews_data:
        # Normalize each professor name for comparison
        if normalize(prof) == prof_query or prof_query in normalize(prof):
            found_prof = prof
            break
            
    # If not found, try checking if any word in professor name matches
    if not found_prof:
        prof_words = prof_query.split()
        for prof in reviews_data:
            for word in prof_words:
                if word and len(word) > 2 and word in normalize(prof):
                    found_prof = prof
                    break
            if found_prof:
                break
                
    updates = {}
    if found_prof:
        # Format reviews
        message = f"📝 Reviews for Professor {found_prof}:\n\n"
        
        # Group reviews by course
        for course, reviews in reviews_data[found_prof].items():
            message += f"Course: {course}\n"
            for i, review in enumerate(reviews, 1):
                # Limit review length if too long
                if len(review) > 300:
                    review = review[:297] + "..."
                message += f"Review {i}: {review}\n\n"
                
        # Store professor name in user data
        updates['lastProfessor'] = found_prof
        
        # Update professor history
        prof_history = user_data.get('professorHistory', [])
        if isinstance(prof_history, str):
            try:
                prof_history = json.loads(prof_history)
            except:
                prof_history = []
                
        if not isinstance(prof_history, list):
            prof_history = []
            
        if found_prof not in prof_history:
            prof_history.append(found_prof)
            updates['professorHistory'] = prof_history[:10]  # Keep last 10 professors
        
        return message, updates
    else:
        return f"❌ No reviews found for professor '{professor_name}'.", updates

def suggest_next_action(user_data):
    """Generate personalized follow-up suggestions based on user history"""
    suggestions = []
    
    # Handle cases where JSON strings need to be parsed
    for key in ['coursesHistory', 'professorHistory', 'lastInstructors']:
        if key in user_data and isinstance(user_data[key], str):
            try:
                user_data[key] = json.loads(user_data[key])
            except:
                user_data[key] = []
    
    # If the user has previously searched for courses but hasn't checked details
    if 'lastCourseCode' in user_data:
        suggestions.append(f"Would you like to get more details about {user_data['lastCourseCode']}?")
    
    # If the user has previously looked at instructors but hasn't checked reviews
    if 'lastProfessor' in user_data and ('professorHistory' not in user_data or not user_data['professorHistory']):
        suggestions.append(f"Would you like to see student reviews for Professor {user_data['lastProfessor']}?")
    
    # If user has looked at multiple courses, suggest comparing them
    courses = user_data.get('coursesHistory', [])
    if isinstance(courses, list) and len(courses) >= 2:
        suggestions.append(f"Would you like to compare {courses[-1]} with {courses[-2]}?")
    
    if suggestions:
        return "\n\nI can also help you with:\n- " + "\n- ".join(suggestions)
    return ""

def handle_check_clashes(event, user_data, slots):
    """Determine a clash-free combination of sections for 2–4 courses."""
    # 1. Safely pull out up to four course slots
    raw_courses = []
    for slot_name in ('course1','course2','course3','course4'):
        slot_obj = slots.get(slot_name) or {}                        # <-- never None
        val_obj  = slot_obj.get('value') or {}                        # <-- never None
        # Lex V2 uses 'interpretedValue'; fallback to 'originalValue'
        course_id = val_obj.get('interpretedValue') \
                    or val_obj.get('originalValue')
        if course_id:
            raw_courses.append(course_id.strip())

    # 2. Enforce at least two courses
    if len(raw_courses) < 2:
        raise ValueError("Please specify at least two courses to check for clashes.")

    # 3. (rest of your existing logic stays exactly the same)
    data = fetch_course_data()
    found_courses = []
    for ci in raw_courses:
        query = normalize(ci)
        match = next(
            (c for c in data
             if normalize(c['course_code']) == query
             or query in normalize(c['course_name'])),
            None
        )
        if not match:
            raise ValueError(f"Course '{ci}' not found in data.")
        found_courses.append(match)

    # 4. Build per-course section combos...
    per_course_choices = []
    for course in found_courses:
        secs = course.get('sections', [])
        lec  = [s for s in secs if s['section_name'].upper().startswith('L')]
        tut  = [s for s in secs if s['section_name'].upper().startswith('T')]
        prac = [s for s in secs if s['section_name'].upper().startswith('P')]
        pools = [lec] + ([tut] if tut else []) + ([prac] if prac else [])
        per_course_choices.append(list(itertools.product(*pools)))

    # 5. Find the first clash‑free combination...
    clash_free = None
    for combo in itertools.product(*per_course_choices):
        all_times = [tt for sel in combo for sec in sel for tt in sec.get('Days_Times',[])]
        if len(all_times) == len(set(all_times)):
            clash_free = combo
            break

    # 6. Return result
    if not clash_free:
        return "⚠️ I’m sorry—no combination of those courses is clash‑free.", {}

    lines = []
    for course, selection in zip(found_courses, clash_free):
        parts = []
        for sec in selection:
            kind  = sec['section_name'][0].upper()
            label = {'L':'Lecture','T':'Tutorial','P':'Practical'}.get(kind,'Section')
            times = ",".join(sec.get('Days_Times', []))
            parts.append(f"{label} {sec['section_name']} ({times})")
        lines.append(f"{course['course_code']} → " + ", ".join(parts))

    message = "✅ Here’s one clash‑free schedule:\n" + "\n".join(f"• {l}" for l in lines)
    return message, {}



def lambda_handler(event, context):
    try:
        print("Event received:", json.dumps(event, indent=2))

        # Extract intent information
        intent_name = event['sessionState']['intent']['name']
        slots = event['sessionState']['intent'].get('slots', {}) or {}
        
        # Generate or extract user ID
        user_id = generate_user_id(event)
        print(f"Processing request for user: {user_id}")
        
        # Get user data from DynamoDB
        user_data = get_user_data(user_id)
        print(f"Retrieved user data: {json.dumps(user_data)}")
        
        # Handle the intent
        if intent_name == "CheckCourseAvailibility" or intent_name == "CheckCourseAvailability":
            message, updates = handle_check_course_availability(event, user_data, slots)
        elif intent_name == "GetCourseDetails" or intent_name == "GetCourseDetailsIntent":
            message, updates = handle_get_course_details(event, user_data, slots)
        elif intent_name == "GetProfReviews":
            message, updates = handle_get_prof_reviews(event, user_data, slots)
        elif intent_name == "checkClashes":
            message, updates = handle_check_clashes(event, user_data, slots)
        else:
            message = "Can you please be a bit more specific?"
            # raise ValueError(f"Unsupported intent: {intent_name}")
        
        # Add personalized suggestions based on user history
        # suggestions = suggest_next_action({**user_data, **updates})
        # message += suggestions
            
        # Update user data in DynamoDB
        if updates:
            update_user_data(user_id, updates)

        response = {
            "sessionState": {
                "dialogAction": {
                    "type": "Close"
                },
                "intent": {
                    "name": intent_name,
                    "state": "Fulfilled", 
                    "slots": slots
                },
                "sessionAttributes": {
                    "userId": user_id  # Store user ID in session for future reference
                }
            },
            "messages": [
                {
                    "contentType": "PlainText",
                    "content": message
                }
            ]
        }

        print("Response being sent:", json.dumps(response, indent=2))
        return response

    except Exception as e:
        import traceback
        print(f"Exception occurred: {str(e)}")
        print(traceback.format_exc())
        
        # Try to extract user ID for the error case
        try:
            user_id = generate_user_id(event)
            session_attrs = {"userId": user_id}
        except:
            session_attrs = {}
        
        error_response = {
            "sessionState": {
                "dialogAction": {
                    "type": "Close"
                },
                "intent": {
                    "name": event['sessionState']['intent']['name'],
                    "state": "Failed",
                    "slots": slots
                },
                "sessionAttributes": session_attrs
            },
            "messages": [
                {
                    "contentType": "PlainText",
                    "content": f"⚠️ Error: {str(e)}"
                }
            ]
        }
        print("Error response:", json.dumps(error_response, indent=2))
        return error_response