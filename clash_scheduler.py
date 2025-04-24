import json
from collections import defaultdict
from itertools import product
import requests

class CourseScheduler:
    def __init__(self, json_data):
        self.courses = self._process_json(json_data)
        self.time_slots = self._create_time_slots()
        
    def _process_json(self, json_data):
        """Process raw JSON into structured course data"""
        courses = {}
        for course in json_data:
            code = course['course_code']
            courses[code] = {
                'name': course['course_name'],
                'lectures': [],
                'tutorials': [],
                'practicals': []
            }
            
            # Process sections
            for section in course.get('sections', []):
                section_type = section['section_name'][0].upper()  # L, T, or P
                instructor = section['Instructor']
                room = section['Room']
                time_slots = self._parse_time_slots(section['Days_Times'])
                
                section_data = {
                    'section_name': section['section_name'],
                    'instructor': instructor,
                    'room': room,
                    'time_slots': time_slots
                }
                
                if section_type == 'L':
                    courses[code]['lectures'].append(section_data)
                elif section_type == 'T':
                    courses[code]['tutorials'].append(section_data)
                elif section_type == 'P':
                    courses[code]['practicals'].append(section_data)
                    
        return courses
    
    def _create_time_slots(self):
        """Create a mapping of days to time slots"""
        days = ['M', 'T', 'W', 'Th', 'F']
        hours = range(1, 10)  # Assuming 9 time slots per day
        return {(day, hour): [] for day in days for hour in hours}
    
    def _parse_time_slots(self, days_times):
        """Convert days_times format to standardized time slots"""
        slots = []
        for dt in days_times:
            day, hour = dt.split('_')
            # Handle 'Th' for Thursday
            day = 'Th' if day == 'Th' else day[0]
            slots.append((day, int(hour)))
        return slots
    
    def _check_clash(self, schedule):
        """Check if any time slots clash in the given schedule"""
        occupied = defaultdict(list)
        for course_code, sections in schedule.items():
            for section in sections:
                for day, hour in section['time_slots']:
                    if (day, hour) in occupied:
                        return True
                    occupied[(day, hour)].append({
                        'course': course_code,
                        'section': section['section_name']
                    })
        return False
    
    def _get_section_choices(self, course_code):
        """Get all possible section choices for a course"""
        course = self.courses[course_code]
        choices = []
        if course['lectures']:
            choices.append(('lecture', course['lectures']))
        if course['tutorials']:
            choices.append(('tutorial', course['tutorials']))
        if course['practicals']:
            choices.append(('practical', course['practicals']))
        return choices
    
    def find_non_clashing_schedule(self, selected_courses):
        """Find a non-clashing schedule for selected courses"""
        valid_schedules = []
        
        for course_code in selected_courses:
            if course_code not in self.courses:
                print(f"Warning: Course {course_code} not found in data")
                return None
        
        all_combinations = []
        for course_code in selected_courses:
            course_combinations = []
            choices = self._get_section_choices(course_code)
            for section_type, sections in choices:
                for section in sections:
                    course_combinations.append((course_code, section))
            all_combinations.append(course_combinations)
        
        for combination in product(*all_combinations):
            schedule = defaultdict(list)
            for course_code, section in combination:
                schedule[course_code].append(section)
            
            if not self._check_clash(schedule):
                valid_schedules.append(dict(schedule))
                
                # Return first valid schedule found
                return dict(schedule)
        
        if not valid_schedules:
            print("No non-clashing schedule found for the selected courses")
            return None
    
    def print_schedule(self, schedule):
        """Print the schedule in a readable format"""
        if not schedule:
            print("No valid schedule to display")
            return
            
        print("\nNon-clashing Schedule:")
        print("-" * 50)
        for course_code, sections in schedule.items():
            print(f"\nCourse: {course_code} - {self.courses[course_code]['name']}")
            for section in sections:
                print(f"  Section: {section['section_name']}")
                print(f"  Type: {'Lecture' if section['section_name'][0] == 'L' else 'Tutorial' if section['section_name'][0] == 'T' else 'Practical'}")
                print(f"  Instructor: {section['instructor']}")
                print(f"  Room: {section['room']}")
                print("  Timings:")
                for day, hour in section['time_slots']:
                    print(f"    {day}: Slot {hour}")
        print("-" * 50)

if __name__ == "__main__":
    url = "https://bits-course-data.s3.us-east-1.amazonaws.com/courses_output.json"
    response = requests.get(url)
    response.raise_for_status()
    courses = response.json()
    data=courses
    print(data)
    scheduler = CourseScheduler(data)
    
    print("Available courses:")
    for code in scheduler.courses:
        print(f"- {code}: {scheduler.courses[code]['name']}")
    
    selected = input("\nEnter course codes (comma separated): ").strip().upper().split(',')
    selected = [code.strip() for code in selected]
    
    schedule = scheduler.find_non_clashing_schedule(selected)
    scheduler.print_schedule(schedule)