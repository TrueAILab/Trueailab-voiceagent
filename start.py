"""
Starts the FastAPI server and opens an ngrok tunnel.
Usage: python start.py
"""
import subprocess
import sys
import time
import os
from pyngrok import ngrok, conf

# Load env vars from .env.local if present
from dotenv import load_dotenv
load_dotenv(".env.local")

# Optional: set your ngrok authtoken if you have one
# ngrok.set_auth_token("your_ngrok_authtoken")

PORT = 8000

def main():
    # Start ngrok tunnel
    tunnel = ngrok.connect(PORT, "http")
    public_url = tunnel.public_url
    # Ensure https
    if public_url.startswith("http://"):
        public_url = public_url.replace("http://", "https://", 1)

    print("=" * 60)
    print(f"  ngrok tunnel:    {public_url}")
    print(f"  Twilio webhook:  {public_url}/incoming-call")
    print("=" * 60)
    print()

    # Start uvicorn
    try:
        subprocess.run(
            [sys.executable, "-m", "uvicorn", "server:app",
             "--host", "0.0.0.0", "--port", str(PORT)],
            check=True,
        )
    except KeyboardInterrupt:
        pass
    finally:
        ngrok.kill()

if __name__ == "__main__":
    main()
