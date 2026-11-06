from redis.cluster import RedisCluster, ClusterNode
from aiserver_utils.aescipher import AESCipher
import os

cipher = AESCipher()
password = cipher.decrypt("U/k0TIWVQ+vfVqTNOiOO0w==")
redis_nodes = "localhost:4000,localhost:4001,localhost:4002"

startup_nodes = []
for n in redis_nodes.split(","):
    host, port = n.split(':')
    startup_nodes.append(ClusterNode(host=host, port=port))

r = RedisCluster(startup_nodes=startup_nodes, password=password, decode_responses=True)

queue = "yce_ai:aiHair:queue"

# Queue 長度 (sorted set 用 zcard)
print("Queue length:", r.zcard(queue))

# 前 10 筆 (sorted set 用 zrange)
print(r.zrange(queue, 0, 9))
