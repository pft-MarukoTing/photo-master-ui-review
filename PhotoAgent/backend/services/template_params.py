"""Base engine parameter template for photoAgent.

Mirrors MTVideoAI/backend/services/template_params.py.
"""


def get_engine_params_template():
    return {
        "session_id": "guid",
        "tskId": "taskId",
        "srcKeys": ["demo/photoAgent/input/{msg_id}/src.jpg"],
        "dstKeys": ["demo/photoAgent/result/{msg_id}/result.png"],
        "effect": "photoAgent",
        "acts": [{"id": 0, "params": {}}],
    }
