with open('alembic/env.py', 'r') as f:
    env = f.read()

# We need to correctly set up the path to fix ModuleNotFoundError
new_env = env.replace('import sys\nimport os\nsys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))', 'import sys\nimport os\nsys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))')

new_env = new_env.replace('from matching_bot_project.database.session import Base', 'from database.session import Base')
new_env = new_env.replace('from matching_bot_project.database.models.models import *', 'from database.models.models import *')

with open('alembic/env.py', 'w') as f:
    f.write(new_env)

print("Fixed module path in env.py")
