docker stop photo-agent-chat-peggy-dev; docker rm photo-agent-chat-peggy-dev
docker build -t photo-agent-chat-peggy-dev:test ./
docker run -itd --net=host -v /data:/data --name=photo-agent-chat-peggy-dev photo-agent-chat-peggy-dev:test
