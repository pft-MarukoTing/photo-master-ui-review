docker stop photo-agent; docker rm photo-agent
docker build -t photo-agent:test ./
docker run -itd --net=host -v /data:/data --name=photo-agent photo-agent:test
