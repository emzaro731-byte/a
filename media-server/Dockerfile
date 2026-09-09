FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04
WORKDIR /app
RUN apt-get update && apt-get install -y python3 python3-pip ffmpeg libsndfile1 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt scipy
COPY main.py .
ENV HF_HOME=/models TRANSFORMERS_CACHE=/models MEDIA_OUTPUT_DIR=/outputs
EXPOSE 8100
CMD ["python3", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8100"]
