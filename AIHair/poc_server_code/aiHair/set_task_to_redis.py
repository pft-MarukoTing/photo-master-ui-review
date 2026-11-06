import uuid
from datetime import datetime
import json
from redis.cluster import RedisCluster, ClusterNode
from aiserver_utils.aescipher import AESCipher
from aiserver_utils.s3 import S3Access

MSG_ID_QUEUE = 'yce_ai:aiHair:queue'
MSG_DETAIL_QUEUE = 'yce_ai:aiHair:{msg_id}:info'

cipher = AESCipher()
password = cipher.decrypt("U/k0TIWVQ+vfVqTNOiOO0w==")
redis_nodes = "localhost:4000,localhost:4001,localhost:4002"

startup_nodes = []
for n in redis_nodes.split(","):
    host, port = n.split(':')
    startup_nodes.append(ClusterNode(host=host, port=port))

redis_conn = RedisCluster(startup_nodes=startup_nodes, password=password, decode_responses=True)

s3_accessor = S3Access()

def get_engine_params_template():
    params_dict = {
        "session_id": "guid",
        "tskId": "taskId",
        "srcKeys": [],
        "dstKeys": ["demo/aiHair/result/{msg_id}/result.jpg"],
        "effect": "",
        "acts": [
            {
                "id": 0,
                "params": {
                }
            }
        ]
    }

    return params_dict

def get_ai_hair_params_template(feature="frizzCare", src_key="demo/aiHair/src/{msg_id}/photo.jpg", level=None, n_results=1, ext="jpg"):
    """One aiHair job: `feature` (frizzCare, plump, fluffy, hairline, soft,
    hairpart, grayHair, rootRetouch, strayHair) on the photo at `src_key`,
    `n_results` result images; hairline takes `level` 1 or 2."""
    params_dict = get_engine_params_template()
    params_dict['effect'] = "aiHair"
    params_dict["srcKeys"] = [src_key]
    params_dict['dstKeys'] = [f"demo/aiHair/result/{{msg_id}}/result_{i}.{ext}" for i in range(n_results)]
    params = {"feature": feature}
    if level is not None:
        params["level"] = level
    params_dict['acts'][0]['params'] = params

    return params_dict


def get_ai_hair_inference_style_params_template(src_key="demo/aiHair/src/{msg_id}/photo.jpg"):
    params_dict = get_ai_hair_params_template("soft", src_key)
    params_dict['acts'][0]['params']["inference_style_list"] = [
        {
            "style_id": "aiHair_designer_demo",
            "items": [
                {
                    "feature": "hairline",
                    "level": 2,
                }
            ],
        }
    ]
    return params_dict

def get_session_id():
    now = datetime.utcnow()
    y = now.strftime('%Y')
    m = now.strftime('%m')
    d = now.strftime('%d')
    _time = now.strftime('_%H_%M_%S_')
    session_id = y + m + d + _time + str(uuid.uuid4())
    return session_id

def get_timestamp():
    now = datetime.utcnow()
    timestamp = now.strftime('%Y')[2:]+now.strftime('%m%d%H%M%S.%f')
    timestamp = float(timestamp)
    return timestamp

def enqueue(engine_param_dict, content):
    job_id = get_session_id()
    timestamp = get_timestamp()

    for key in engine_param_dict:
        if key in ["srcKeys", "dstKeys"]:
            for k in range(len(engine_param_dict[key])):
                engine_param_dict[key][k] = engine_param_dict[key][k].replace("{msg_id}", f"{job_id}")

    srcKeys = engine_param_dict["srcKeys"]
    if content:
        engine_param_dict["srcKeys"] = srcKeys[:len(content)]
        content = content[:len(srcKeys)]
        s3_accessor.upload_to_s3(content, srcKeys)
    # Without content, srcKeys are photos already on S3 (aiHair needs one).

    upload_dict = engine_param_dict.copy()
    upload_dict['srcKeys'] = json.dumps(engine_param_dict['srcKeys'])
    upload_dict['dstKeys'] = json.dumps(engine_param_dict['dstKeys'])
    upload_dict['acts'] = json.dumps(engine_param_dict['acts'])
    redis_conn.hset(MSG_DETAIL_QUEUE.replace("{msg_id}", f"{job_id}"), mapping=upload_dict)
    redis_conn.zadd(MSG_ID_QUEUE, {job_id: timestamp})
    return job_id

if __name__ == "__main__":
    enqueue(get_ai_hair_params_template(), None)
