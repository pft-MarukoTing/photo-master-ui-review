import os
import random
import uuid
from aiserver_utils.logger import logger
from aiserver_utils.otel_metrics_client import OTelMetricsClient

class MetricsLogger:
    _instance = None

    def __new__(cls):
        if cls._instance is not None:
            return cls._instance

        self = super().__new__(cls)
        cls._instance = self
        self.initialized = False
        self.service_name = "gen-ai"
        self.collector_endpoint = os.getenv("OLTP_COLLECTOR_ENDPOINT", "http://localhost:4318/v1/metrics")
        self.service_version = "1.0.0"
        self.environment = "production"
        self.export_interval_millis = int(os.getenv("OTEL_EXPORT_INTERVAL_SECONDS", "60"))  * 1000 # Export every * seconds

        return cls._instance

    def get_instance_info(self) -> str:
        instance_id = os.environ.get('HOSTNAME', None)
        if instance_id is None:
            instance_id = f"unknown-prod-engine-unknown-unknown-{''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=10))}-{''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=5))}"
        parts = instance_id.split("-")
        
        self.unique_id = uuid.uuid4()
        if len(parts) >= 5:
            apartment = parts[0]
            env = parts[1]
            cluster_type = parts[3]
            region = parts[4]
            
            # Determine provider and gpu based on cluster_type and length
            gpu = "unknown"
            if cluster_type == "hcs":
                if len(parts) == 7:
                    provider = parts[5]
                    self.unique_id = "-".join(parts[6:])
                elif len(parts) == 8:
                    gpu = parts[5]
                    provider = parts[6]
                    self.unique_id = "-".join(parts[7:])
                else:
                    provider = "unknown"
            else:
                provider = "aws"
                self.unique_id = "-".join(parts[5:])
            # Store parsed components
            return {
                "apartment": apartment,
                "env": env,
                "cluster_type": cluster_type,
                "provider": provider,
                "gpu": gpu,
                "region": region,
                "instance_id": str(self.unique_id)
            }
        else:
            # Not enough parts to parse
            return {
                "apartment": "unknown",
                "env": "unknown",
                "cluster_type": "unknown",
                "provider": "unknown",
                "gpu": "unknown",
                "region": "unknown",
                "instance_id": str(self.unique_id)
            }

    def initialize(self, metrics):
        if self.initialized:
            raise Exception("MetricsLogger has already been initialized.")
        logger.log(f"[metrics] init with {metrics}", status="running")
        
        self.otel_client = OTelMetricsClient(
            service_name=self.service_name,
            service_version=self.service_version,
            environment=self.environment,
            export_interval_millis=self.export_interval_millis,
            collector_endpoint=self.collector_endpoint
        )
        
        # Modify metrics before initializing
        if metrics is None:
            metrics = {}
        if "metrics_table" not in metrics:
            metrics["metrics_table"] = {}
        metrics_table = metrics["metrics_table"]
            
        # Check for duplicate keys in histogram
        if "histogram" not in metrics_table:
            metrics_table["histogram"] = []
        if "gauge" not in metrics_table:
            metrics_table["gauge"] = []

        all_histogram_keys = metrics_table["histogram"]
        for item in all_histogram_keys:
            for effect_name, metrics_dict in item.items():
                if "queueing_time" in metrics_dict:
                    raise ValueError(f"Duplicate key 'queueing_time' already exists in histogram for effect '{effect_name}'")
                metrics_dict["queueing_time"] = 100000
        
        gauge_keys = metrics_table["gauge"]
        if "host_utilization" in gauge_keys:
            raise ValueError("Duplicate key 'host_utilization' already exists in gauge")
        
        gauge_keys.append("host_utilization")
        
        self.otel_client.initialize(metrics)
        self.initialized = True
        self.attributes = self.get_instance_info()  # Default attributes can be set here

    def log_metrics(self, type, name, effect, value):
        if not self.initialized:
            logger.log("[metrics] not yet been initialized.")
            return
        logger.log(f"[metrics] log with type={type}, name={name}, effect={effect}, value={value}", status="running")
        self.otel_client.log_metrics(type, name, effect, value, attributes=self.attributes)

    def shutdown(self):
        if self.initialized:
            logger.log("[metrics] shutting down metrics logger...", status="running")
            self.otel_client.shutdown()
            self.initialized = False
            logger.log("[metrics] metrics logger shut down.", status="success")

metrics_logger = MetricsLogger()
