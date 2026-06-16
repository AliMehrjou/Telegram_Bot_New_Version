import re

file_path = 'bot/middlewares/anti_spam.py'

with open(file_path, 'r') as f:
    content = f.read()

# Anti_spam doesn't have session. Database middleware does. Let's do it in database.py
