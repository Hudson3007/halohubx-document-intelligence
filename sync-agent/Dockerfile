FROM python:3.11-slim

WORKDIR /srv
COPY agent.py .
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Use CMD (not ENTRYPOINT) so docker-compose's `command:` fully overrides the
# default instead of being appended as extra args.
CMD ["python", "agent.py"]