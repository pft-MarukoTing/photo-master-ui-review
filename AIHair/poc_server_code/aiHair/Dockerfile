FROM ubuntu:22.04

ENV LANG=C.UTF-8 LC_ALL=C.UTF-8
# numpy asks the kernel for transparent hugepages on every large array
# (madvise); with THP defrag=madvise, as Ubuntu has it, each such allocation
# may stall on memory compaction. Off, the features' local steps ran about 9x
# faster on the dev server (output/profile_20261109/algorithms), their
# results unchanged.
ENV NUMPY_MADVISE_HUGEPAGE=0

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

# The face model (hairfx/landmarks.py: face_alignment's FAN, CPU torch), run
# in this python, in the process. face-alignment without its deps: it asks
# for opencv-python, which would clash with requirements.txt's
# opencv-python-headless (the same cv2, and it needs libGL). Its weights
# (s3fd, 2DFAN4) are fetched here into TORCH_HOME, so a container needn't
# download them, whichever user it runs as.
ENV TORCH_HOME=/app/torch_cache HAIR_FACE_PYTHON=/usr/bin/python3
RUN pip3 install --index-url https://download.pytorch.org/whl/cpu torch==2.5.1 && \
    pip3 install --no-deps face-alignment==1.5.0 && \
    pip3 install numpy==2.2.6 scipy==1.15.3 scikit-image==0.25.2 numba==0.68.1 \
        tqdm==4.70.1 packaging && \
    python3 -c "import face_alignment as fa; fa.FaceAlignment(fa.LandmarksType.TWO_D, device='cpu', face_detector='sfd', flip_input=False, compile=False)"

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
# The hair model (models/, TensorFlow) and the face model (face_alignment,
# above) both run in the server, each loaded once at its start.
ENV AIHAIR_TIMING_LOG=/app/log/aihair_timing.jsonl

EXPOSE 80

ENTRYPOINT [ "/usr/bin/tini", "--", "/app/entrypoint.sh" ]
