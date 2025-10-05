import os
from app import app as application # IMPORTANT: This imports your 'app' instance and names it 'application'
# The 'application' variable is what Elastic Beanstalk expects to run.

# Optional: Set a temporary default for local development if not using a .env file
# if __name__ == "__main__":
#     # You can run app.py directly for local development if you prefer
#     # but on EB, Gunicorn runs the 'application' object.
#     pass