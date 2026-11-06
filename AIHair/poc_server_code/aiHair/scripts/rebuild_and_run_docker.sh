docker stop aihair; docker rm aihair
docker build -t aihair:test .
docker run -itd --net=host -v /data:/data -e ENGINE_WORKER_NUM="${ENGINE_WORKER_NUM:-20}" --name=aihair aihair:test
