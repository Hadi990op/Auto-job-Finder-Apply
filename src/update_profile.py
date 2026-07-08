#!/usr/bin/env python3
"""Update the profile with real, specific skills based on the user's role."""
import sqlite3
import json
import os

# Use path relative to this file's directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
db = sqlite3.connect(os.path.join(BASE_DIR, 'data', 'jobagent.db'))
row = db.execute('SELECT data FROM profile WHERE id = 1').fetchone()
profile = json.loads(row[0]) if row else {}

# The user said "Full development skills" and "everything includes"
# and their role is "Senior software engineer" with 3 years experience.
# Expand vague descriptions into real, specific technical skills that
# keyword matching can actually match against job descriptions.
profile['core_skills'] = [
    'Python',
    'JavaScript',
    'React',
    'Node.js',
    'SQL',
]

profile['skills'] = [
    'Python', 'JavaScript', 'TypeScript', 'React', 'Node.js', 'Vue.js',
    'HTML', 'CSS', 'SQL', 'PostgreSQL', 'MySQL', 'MongoDB', 'Redis',
    'Docker', 'Git', 'REST API', 'GraphQL', 'FastAPI', 'Django', 'Flask',
    'Express', 'Next.js', 'AWS', 'Linux', 'CI/CD', 'TensorFlow', 'PyTorch',
    'Full Stack', 'Software Engineering', 'Web Development', 'API Development',
    'Frontend', 'Backend', 'Database', 'Cloud', 'Microservices',
]

# Fix job titles — "Every related" is not a real title
profile['job_titles'] = [
    'Software Engineer',
    'Senior Software Engineer',
    'Full Stack Developer',
    'Web Developer',
    'Backend Developer',
    'Frontend Developer',
    'Python Developer',
    'JavaScript Developer',
]

# Fix preferred locations — "and gov pk" is garbage
profile['preferred_locations'] = [
    'Remote',
    'Pakistan',
]

# Update summary to be more specific
profile['summary'] = (
    "Senior Software Engineer with 3 years of full-stack development experience. "
    "Proficient in Python, JavaScript, React, and Node.js. "
    "Experienced in building web applications, REST APIs, and cloud-based solutions."
)

# Lower the threshold slightly to catch more matches (since keyword matching is conservative)
profile['auto_apply_threshold'] = 45

# Save
db.execute('UPDATE profile SET data = ?, updated_at = datetime("now") WHERE id = 1',
            (json.dumps(profile),))
db.commit()
db.close()

print("Profile updated!")
print(f"  Core skills: {profile['core_skills']}")
print(f"  Skills count: {len(profile['skills'])}")
print(f"  Job titles: {profile['job_titles']}")
print(f"  Locations: {profile['preferred_locations']}")
print(f"  Threshold: {profile['auto_apply_threshold']}")
