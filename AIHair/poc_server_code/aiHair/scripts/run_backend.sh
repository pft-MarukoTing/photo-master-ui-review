#!/bin/bash

export FLASK_APP=backend.py;
export FLASK_ENV=development;
# export FLASK_DEBUG=1;

export CUDA_VISIBLE_DEVICES=3;

flask run  --host=0.0.0.0 --cert=/disk/RDAIMT/wwwroot/nginx/ssl/localhost+3.pem --key=/disk/RDAIMT/wwwroot/nginx/ssl/localhost+3-key.pem -p 8080
