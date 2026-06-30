#!/bin/bash

export CUDA_VISIBLE_DEVICES=3;

uvicorn server:app --host 0.0.0.0 --port 8000 \
  --ssl-certfile=/disk/RDAIMT/wwwroot/nginx/ssl/localhost+3.pem \
  --ssl-keyfile=/disk/RDAIMT/wwwroot/nginx/ssl/localhost+3-key.pem \
  --timeout-keep-alive 310
