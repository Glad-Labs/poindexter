# Use the official lightweight Python image.
# https://hub.docker.com/_/python
FROM python:3.11-slim

# Set environment variables to prevent writing .pyc files and to buffer output correctly
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set the working directory in the container
WORKDIR /usr/src/app

# Copy only the requirements file to leverage Docker's cache.
# This layer is only rebuilt when requirements.txt changes.
COPY requirements.txt .

# Install the dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application's code into the container.
# Files listed in .dockerignore will be excluded.
COPY . .

# Command to run the application
CMD ["python", "orchestrator.py"]