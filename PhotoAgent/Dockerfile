# Build from PhotoAgent/ directory:
#   cd PhotoAgent
#   docker build -t photo-agent .
#
# NOTE: aiserver_utils/ must be present in this directory before building.
#   Copy it from the sibling aiSong project:
#   cp -r ../aiSong/aiserver_utils ./aiserver_utils

FROM ubuntu:22.04

ENV LANG=C.UTF-8 LC_ALL=C.UTF-8

RUN apt-get clean && apt-get update --fix-missing && \
    DEBIAN_FRONTEND=noninteractive \
    apt-get install -y \
        python3.10 wget bzip2 ca-certificates curl git nginx supervisor \
        python3-pip tzdata software-properties-common && \
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

# Configure nginx
COPY nginx.conf /etc/nginx/nginx.conf

# Create working directory
RUN mkdir /app
WORKDIR /app

# Application files
COPY .env.default        /app/
COPY entrypoint.sh       /app/
COPY supervisord.conf    /app/
COPY load_env.py         /app/
COPY server_logger.py    /app/
COPY engine_result_code.py /app/
COPY artifact_store.py   /app/
COPY config.py           /app/
COPY prompts.py          /app/
COPY image_tools.py      /app/
COPY sdl_face_detector.py /app/
COPY models              /app/models
COPY openai_client.py    /app/
COPY gemini_client.py   /app/
COPY pipeline.py         /app/
COPY job_processor.py    /app/
COPY server.py           /app/
COPY set_task_to_redis.py /app/
COPY enqueue_example.py /app/
COPY sample_images /app/sample_images
COPY aiserver_utils /app/aiserver_utils
COPY photo_agent         /app/photo_agent

RUN chmod +x /app/entrypoint.sh && \
    mkdir -p /app/log /app/tmp

EXPOSE 80

ENTRYPOINT ["/usr/bin/tini", "--", "/app/entrypoint.sh"]
