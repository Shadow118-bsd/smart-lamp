# Use official Python 3.10 slim image
FROM python:3.10-slim

# Install system audio & build dependencies (PortAudio, ALSA, FFmpeg, GCC)
RUN apt-get update && apt-get install -y --no-install-recommends \
    portaudio19-dev \
    libasound2-dev \
    ffmpeg \
    gcc \
    g++ \
    make \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install python packages
COPY scratch/requirements.txt ./scratch/requirements.txt
RUN pip install --no-cache-dir -r scratch/requirements.txt

# Copy application source code
COPY . .

# Expose Web Dashboard Port (8088), UDP Audio Port (12345), UDP Event Port (12346)
EXPOSE 8088 12345/udp 12346/udp

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV OLLAMA_HOST=http://ollama:11434

# Run the Smart Lamp Server
CMD ["python", "scratch/udp_receiver.py"]
