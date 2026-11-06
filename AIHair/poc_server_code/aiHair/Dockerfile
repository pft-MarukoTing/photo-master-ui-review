FROM ubuntu:22.04

ENV LANG=C.UTF-8 LC_ALL=C.UTF-8

RUN apt-get clean && apt-get update --fix-missing && \
    DEBIAN_FRONTEND=noninteractive \
    apt-get install -y \
        python3.10 wget bzip2 ca-certificates curl git nginx supervisor\
        python3-pip tzdata software-properties-common ffmpeg && \
    TZ=Asia/Taipei && \
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && \
    echo $TZ > /etc/timezone && \
    dpkg-reconfigure -f noninteractive tzdata && \
    apt-get clean

# Install Python dependencies
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --upgrade pip setuptools wheel && \
    pip3 install -r /tmp/requirements.txt && \
    rm /tmp/requirements.txt

# Install Tini
ENV TINI_VERSION v0.19.0
ADD https://github.com/krallin/tini/releases/download/${TINI_VERSION}/tini /usr/bin/tini
RUN chmod +x /usr/bin/tini

# Create a working directory
RUN mkdir /app
WORKDIR /app

# Copy your file list into the container
COPY aiserver_utils /app/aiserver_utils
COPY server_logger.py /app/
COPY .env.default /app/
COPY entrypoint.sh /app/
COPY server.py /app/
COPY artifact_store.py /app/
COPY job_processor.py /app/
COPY memory_utils.py /app/
COPY config.py /app/
COPY ai_hair /app/ai_hair
COPY hairfx /app/hairfx
COPY bundle /app/bundle
COPY models /app/models
COPY c2pa /workspace/c2pa
COPY secret_manager /app/secret_manager
COPY engine_result_code.py /app/
COPY set_task_to_redis.py /app/
COPY enqueue_example.py /app/
COPY upload_file_from_s3.py /app/
COPY batch_main.py /app/
COPY supervisord.conf /app/
COPY load_env.py /app/
RUN chmod +x /app/entrypoint.sh
RUN mkdir -p /app/log
# The hair model (models/, TensorFlow) runs in the server; the face model
# (face_alignment, OpenCV) in a python of its own: point HAIR_FACE_PYTHON at
# it (.env.default).
ENV AIHAIR_TIMING_LOG=/app/log/aihair_timing.jsonl

EXPOSE 80

ENTRYPOINT [ "/usr/bin/tini", "--", "/app/entrypoint.sh" ]
