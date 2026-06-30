"""
OpenTelemetry Metrics Client Library (Push Mode)

A simple, intuitive wrapper for OpenTelemetry metrics that:
- Pushes metrics to a remote CloudMap Metrics Server via HTTP
- Automatic histogram bucket calculation based on expected max values
- Easy counter and gauge creation
- Centralized MeterProvider management

Version: 2.2.0 (Push Mode - Client)
"""
from .logger import logger
from typing import Dict, List, Optional, Callable, Any, Tuple, Union
from dataclasses import dataclass

from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import View, ExplicitBucketHistogramAggregation

@dataclass
class HistogramConfig:
    """
    Configuration for a histogram metric.
    
    Attributes:
        name: Metric name (e.g., "http.request.duration")
        description: Human-readable description
        unit: Unit of measurement (e.g., "s" for seconds, "ms" for milliseconds)
        bucket_count: Number of buckets to generate (default: 100)
        min_value: Minimum expected value (default: 0)
        max_value: Maximum expected value (default: 100)
    """
    name: str
    description: str = ""
    unit: str = "s"
    bucket_count: int = 100
    min_value: float = 0.0
    max_value: float = 100.0


@dataclass
class CounterConfig:
    """
    Configuration for a counter metric.
    
    Attributes:
        name: Metric name (e.g., "http.requests.total")
        description: Human-readable description
        unit: Unit of measurement (default: "1" for count)
    """
    name: str
    description: str = ""
    unit: str = "1"


@dataclass
class GaugeConfig:
    """
    Configuration for a gauge metric.
    
    Attributes:
        name: Metric name (e.g., "system.memory.usage")
        description: Human-readable description
        unit: Unit of measurement (e.g., "By" for bytes)
    """
    name: str
    description: str = ""
    unit: str = "1"


class OTelMetricsClient:
    """
    OpenTelemetry Metrics Client Library (Push Mode)
    
    Pushes metrics to a remote CloudMap Metrics Server via HTTP.
    
    PUBLIC INTERFACE:
        - __init__(): Configure the metrics client
        - initialize(metrics_table): Initialize metrics
        - log_metrics(type, name, effect, value, attributes): Log metric values
        - shutdown(): Clean shutdown
    
    Example Usage:
        ```python
        # Load metrics table
        import json
        with open("metrics_table.txt") as f:
            metrics_table = json.load(f)
        
        # Initialize client to push to remote server
        otel = OTelMetricsClient(
            service_name="my-service",
            collector_endpoint="http://localhost:4318/v1/metrics"  # CloudMap server endpoint
        )
        otel.initialize(metrics_table)
        
        # Log metrics - they'll be pushed to the server
        otel.log_metrics(type="histogram", name="processing_time", effect="enhance", value=600)
        otel.log_metrics(type="counter", name="success", effect="enhance", value=1)
        otel.log_metrics(type="gauge", name="host_utilization", effect="enhance", value=75)
        
        # Cleanup
        otel.shutdown()
        ```
    """
    
    def __init__(
        self,
        service_name: str = "gen-ai",
        collector_endpoint: str = "http://localhost:4318/v1/metrics",

        service_version: str = "1.0.0",
        environment: str = "production",
        export_interval_millis: int = 10000,  # Export every 10 seconds
    ):
        """
        Initialize the OTel Metrics Client with push mode.
        
        Args:
            service_name: Name of your service
            collector_endpoint: HTTP endpoint of the CloudMap Metrics Server (default: http://localhost:4318/v1/metrics)
            service_version: Version of your service
            environment: Environment (production, staging, etc.)
            export_interval_millis: How often to push metrics (default: 10000ms = 10s)
        """
        self.service_name = service_name
        self.service_version = service_version
        self.environment = environment
        self.collector_endpoint = collector_endpoint
        self.export_interval_millis = export_interval_millis
        
        # Storage for metric configurations
        self._histogram_configs: Dict[str, HistogramConfig] = {}
        self._counter_configs: Dict[str, CounterConfig] = {}
        self._gauge_configs: Dict[str, GaugeConfig] = {}
        self._raw_metrics_table: Optional[Dict] = None  # Store original metrics_table
        
        # Runtime objects (created during initialize())
        self._meter_provider: Optional[MeterProvider] = None
        self._meter = None
        self._histograms: Dict[str, Any] = {}
        self._counters: Dict[str, Any] = {}
        self._gauges: Dict[str, Any] = {}
        # Store gauge values with attributes: {gauge_name: [(value, attributes), ...]}
        self._gauge_values: Dict[str, List[Tuple[float, Dict[str, str]]]] = {}
        self._initialized = False
        
        logger.log(f"OTelMetricsClient configured for service '{service_name}'")
    
    def add_histogram(
        self,
        name: str,
        bucket_count: int = 100,
        description: str = "",
        unit: str = "s",
        min_value: float = 0.0,
        max_value: float = 100.0
    ) -> 'OTelMetricsClient':
        """Add a histogram metric with auto-calculated buckets."""
        self._histogram_configs[name] = HistogramConfig(
            name=name,
            description=description,
            unit=unit,
            bucket_count=bucket_count,
            min_value=min_value,
            max_value=max_value
        )
        return self
    
    def add_counter(
        self,
        name: str,
        description: str = "",
        unit: str = "1"
    ) -> 'OTelMetricsClient':
        """Add a counter metric."""
        self._counter_configs[name] = CounterConfig(
            name=name,
            description=description,
            unit=unit
        )
        return self
    
    def add_gauge(
        self,
        name: str,
        description: str = "",
        unit: str = "1"
    ) -> 'OTelMetricsClient':
        """Add a gauge metric."""
        self._gauge_configs[name] = GaugeConfig(
            name=name,
            description=description,
            unit=unit
        )
        return self
    
    def _calculate_histogram_boundaries(self, config: HistogramConfig) -> List[float]:
        """Calculate histogram bucket boundaries."""
        min_val = config.min_value
        max_val = config.max_value
        count = config.bucket_count
        
        if count <= 1:
            return [max_val]
        
        boundaries = []
        step = (max_val - min_val) / count
        for i in range(count):
            value = min_val + ((i + 1) * step)
            boundaries.append(round(value, 2))
        
        return sorted(set(boundaries))
    
    def _create_gauge_callback(self, name: str) -> Callable:
        """Create a callback function for a gauge."""
        def callback(options):
            # Return all observations with their attributes
            gauge_data = self._gauge_values.get(name, [])
            for value, attrs in gauge_data:
                yield metrics.Observation(value, attrs)
        return callback
    
    def _parse_metrics_table(self, metrics_table: Dict) -> None:
        """Parse metrics table and configure metrics."""
        if not isinstance(metrics_table, dict):
            raise ValueError("metrics_table must be a dictionary")
        
        # Store raw metrics table for server push
        self._raw_metrics_table = metrics_table
        
        table = metrics_table.get("metrics_table", {})
        
        # Parse histograms
        histograms = table.get("histogram", [])
        for histogram_group in histograms:
            if not isinstance(histogram_group, dict):
                continue
            
            for effect_name, metrics_dict in histogram_group.items():
                if not isinstance(metrics_dict, dict):
                    continue
                
                for metric_name, max_value in metrics_dict.items():
                    full_name = f"{effect_name}.{metric_name}"
                    self.add_histogram(
                        name=full_name,
                        max_value=max_value,
                        bucket_count=100,
                        description=f"{metric_name} for {effect_name}",
                        unit="ms"
                    )
        
        # Parse counters
        counters = table.get("counter", [])
        for counter_name in counters:
            if isinstance(counter_name, str):
                self.add_counter(
                    name=counter_name,
                    description=f"{counter_name} counter"
                )
        
        # Parse gauges
        gauges = table.get("gauge", [])
        for gauge_name in gauges:
            if isinstance(gauge_name, str):
                self.add_gauge(
                    name=gauge_name,
                    description=f"{gauge_name} gauge"
                )
    

    
    def _create_meter_provider(self) -> MeterProvider:
        """Create MeterProvider with OTLP HTTP exporter (push mode)."""
        views = []
        existing_histogram_names = set()
        for name, config in self._histogram_configs.items():
            view_name = f"*.{name.split('.')[-1]}"
            if view_name in existing_histogram_names:
                continue
            existing_histogram_names.add(view_name)
            boundaries = self._calculate_histogram_boundaries(config)
            view = View(
                instrument_name=view_name,
                instrument_type=metrics.Histogram,
                aggregation=ExplicitBucketHistogramAggregation(
                    boundaries=boundaries
                )
            )
            views.append(view)
            logger.log(f"Created View for histogram '{view_name}': {len(boundaries)} buckets (max={config.max_value}), boundaries: {boundaries[:5]}...{boundaries[-5:]}")
        
        # Create OTLP HTTP exporter to push to remote server
        exporter = OTLPMetricExporter(
            endpoint=self.collector_endpoint,
            headers={}  # Add auth headers if needed
        )
        
        # Create periodic reader that exports every N milliseconds
        reader = PeriodicExportingMetricReader(
            exporter=exporter,
            export_interval_millis=self.export_interval_millis
        )
        
        # Create MeterProvider with Views
        provider = MeterProvider(
            metric_readers=[reader],
            views=views
        )
        
        logger.log(f"MeterProvider configured with {len(views)} Views, pushing to {self.collector_endpoint}")
        return provider
    
    def _create_instruments(self) -> None:
        """Create all metric instruments."""
        # Create histograms
        for name, config in self._histogram_configs.items():
            self._histograms[name] = self._meter.create_histogram(
                name=name,
                description=config.description,
                unit=config.unit
            )
            logger.log(f"Created histogram instrument: {name} (expecting {config.bucket_count} buckets)")
        
        # Create counters
        for name, config in self._counter_configs.items():
            self._counters[name] = self._meter.create_counter(
                name=name,
                description=config.description,
                unit=config.unit
            )
            logger.log(f"Created counter: {name}")
        
        # Create gauges
        for name, config in self._gauge_configs.items():
            self._gauge_values[name] = []  # Initialize as empty list
            self._gauges[name] = self._meter.create_observable_gauge(
                name=name,
                description=config.description,
                unit=config.unit,
                callbacks=[self._create_gauge_callback(name)]
            )
            logger.log(f"Created gauge: {name}")
    
    def initialize(self, metrics_table: Optional[Dict] = None) -> 'OTelMetricsClient':
        """
        Initialize the MeterProvider and create all configured metrics.
        
        Args:
            metrics_table: Optional dictionary containing metric definitions
        
        Returns:
            self (for method chaining)
        """
        if self._initialized:
            raise RuntimeError("OTelMetricsClient already initialized")
        
        # Parse metrics table if provided
        if metrics_table:
            self._parse_metrics_table(metrics_table)
        
        # Create MeterProvider
        self._meter_provider = self._create_meter_provider()
        
        # Set as global provider
        metrics.set_meter_provider(self._meter_provider)
        
        # Get meter
        self._meter = self._meter_provider.get_meter(
            name=self.service_name,
            version=self.service_version
        )
        
        # Create all instruments
        self._create_instruments()
        self._initialized = True
        
        logger.log(f"OTelMetricsClient initialized for service '{self.service_name}'")
        logger.log(f"Pushing metrics to: {self.collector_endpoint}")
        return self
    
    def log_metrics(
        self,
        type: str,
        name: str,
        effect: str,
        value: Union[int, float],
        attributes: Optional[Dict[str, str]] = None
    ) -> None:
        """
        Log a metric with specified type, name, effect, and value.
        
        Args:
            type: Metric type ("histogram", "counter", or "gauge")
            name: Metric name
            effect: Effect/category name
            value: Value to record
            attributes: Optional additional attributes/tags
        """
        if not self._initialized:
            raise RuntimeError("Must call initialize() before logging metrics")
        
        if type not in ("histogram", "counter", "gauge"):
            raise ValueError(f"Invalid metric type: '{type}'")
        
        attrs = attributes.copy() if attributes is not None else {}
        attrs["effect"] = effect
        
        try:
            if type == "histogram":
                full_name = f"{effect}.{name}"
                if full_name not in self._histograms:
                    raise ValueError(f"Histogram '{full_name}' not found")
                self._histograms[full_name].record(value, attrs)
            
            elif type == "counter":
                if name not in self._counters:
                    raise ValueError(f"Counter '{name}' not found")
                self._counters[name].add(value, attrs)
            
            elif type == "gauge":
                if name not in self._gauges:
                    raise ValueError(f"Gauge '{name}' not found")
                
                # Find existing entry with matching attributes and update, or append new
                gauge_data = self._gauge_values[name]
                updated = False
                for i, (existing_value, existing_attrs) in enumerate(gauge_data):
                    if existing_attrs == attrs:
                        # Update existing entry
                        gauge_data[i] = (value, attrs)
                        updated = True
                        break
                
                if not updated:
                    # Append new entry
                    gauge_data.append((value, attrs))
                
        except Exception as e:
            logger.warning(f"Failed to log metric {type}:{effect}.{name}: {e}")
            raise
    
    def shutdown(self) -> None:
        """Shutdown the MeterProvider and cleanup resources."""
        if not self._initialized:
            return
        
        logger.log("Shutting down OTelMetricsClient...")
        
        if self._meter_provider:
            try:
                self._meter_provider.force_flush()
                self._meter_provider.shutdown()
            except Exception as e:
                logger.warning(f"Error during MeterProvider shutdown: {e}")
        
        self._initialized = False
        logger.log("OTelMetricsClient shutdown complete")
    
    def __enter__(self):
        """Context manager entry"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.shutdown()