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

r.delete("yce_ai:aiHair:queue")
