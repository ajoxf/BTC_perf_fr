"""
Configuration management for the trading system
"""
from dataclasses import dataclass, field
from typing import Optional
import yaml
import os


@dataclass
class OKXConfig:
    """OKX API configuration"""
    api_key: str = ""
    secret_key: str = ""
    passphrase: str = ""
    demo_trading: bool = True
    rate_limit_per_second: int = 10

    @classmethod
    def from_env(cls) -> 'OKXConfig':
        return cls(
            api_key=os.environ.get('OKX_API_KEY', ''),
            secret_key=os.environ.get('OKX_SECRET_KEY', ''),
            passphrase=os.environ.get('OKX_PASSPHRASE', ''),
            demo_trading=os.environ.get('OKX_DEMO', 'true').lower() == 'true'
        )


@dataclass
class BasisStrategyConfig:
    """Spot-Futures basis trading configuration"""
    enabled: bool = True
    lookback_period: int = 168  # hours (7 days)
    entry_zscore: float = 2.0
    exit_zscore: float = 0.5
    max_position_usd: float = 10000.0
    stop_loss_pct: float = 0.02
    time_stop_hours: int = 168


@dataclass
class FundingStrategyConfig:
    """Perpetual funding rate arbitrage configuration"""
    enabled: bool = True
    lookback_periods: int = 21  # 7 days of funding periods
    min_rate_threshold: float = 0.0005  # 0.05%
    entry_zscore: float = 2.0
    exit_zscore: float = 0.5
    max_position_usd: float = 10000.0
    min_settlements: int = 1
    max_settlements: int = 6


@dataclass
class RiskConfig:
    """Risk management configuration"""
    max_total_exposure_usd: float = 25000.0
    max_loss_per_trade_usd: float = 500.0
    max_daily_loss_usd: float = 1000.0
    hurst_threshold: float = 0.5  # Only trade when Hurst < 0.5


@dataclass
class FeeConfig:
    """Trading fee configuration"""
    spot_maker_fee: float = 0.0008  # 0.08%
    spot_taker_fee: float = 0.001   # 0.10%
    futures_maker_fee: float = 0.0002  # 0.02%
    futures_taker_fee: float = 0.0005  # 0.05%
    perp_maker_fee: float = 0.0002
    perp_taker_fee: float = 0.0005


@dataclass
class TradingConfig:
    """Complete trading system configuration"""
    okx: OKXConfig = field(default_factory=OKXConfig)
    basis: BasisStrategyConfig = field(default_factory=BasisStrategyConfig)
    funding: FundingStrategyConfig = field(default_factory=FundingStrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    fees: FeeConfig = field(default_factory=FeeConfig)
    database_path: str = "instance/trading.db"
    log_level: str = "INFO"
    log_file: str = "logs/trading.log"

    @classmethod
    def from_yaml(cls, path: str) -> 'TradingConfig':
        """Load configuration from YAML file"""
        if not os.path.exists(path):
            return cls()

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        config = cls()

        if 'okx' in data:
            config.okx = OKXConfig(**data['okx'])
        config.okx = OKXConfig.from_env()  # Override with env vars

        if 'strategies' in data:
            if 'basis' in data['strategies']:
                config.basis = BasisStrategyConfig(**data['strategies']['basis'])
            if 'funding' in data['strategies']:
                config.funding = FundingStrategyConfig(**data['strategies']['funding'])

        if 'risk' in data:
            config.risk = RiskConfig(**data['risk'])

        if 'fees' in data:
            config.fees = FeeConfig(**data['fees'])

        if 'database' in data:
            config.database_path = data['database'].get('path', config.database_path)

        if 'logging' in data:
            config.log_level = data['logging'].get('level', config.log_level)
            config.log_file = data['logging'].get('file', config.log_file)

        return config

    def to_yaml(self, path: str) -> None:
        """Save configuration to YAML file"""
        data = {
            'okx': {
                'demo_trading': self.okx.demo_trading,
                'rate_limit_per_second': self.okx.rate_limit_per_second
            },
            'strategies': {
                'basis': {
                    'enabled': self.basis.enabled,
                    'lookback_period': self.basis.lookback_period,
                    'entry_zscore': self.basis.entry_zscore,
                    'exit_zscore': self.basis.exit_zscore,
                    'max_position_usd': self.basis.max_position_usd,
                    'stop_loss_pct': self.basis.stop_loss_pct,
                    'time_stop_hours': self.basis.time_stop_hours
                },
                'funding': {
                    'enabled': self.funding.enabled,
                    'lookback_periods': self.funding.lookback_periods,
                    'min_rate_threshold': self.funding.min_rate_threshold,
                    'entry_zscore': self.funding.entry_zscore,
                    'exit_zscore': self.funding.exit_zscore,
                    'max_position_usd': self.funding.max_position_usd,
                    'min_settlements': self.funding.min_settlements,
                    'max_settlements': self.funding.max_settlements
                }
            },
            'risk': {
                'max_total_exposure_usd': self.risk.max_total_exposure_usd,
                'max_loss_per_trade_usd': self.risk.max_loss_per_trade_usd,
                'max_daily_loss_usd': self.risk.max_daily_loss_usd,
                'hurst_threshold': self.risk.hurst_threshold
            },
            'fees': {
                'spot_maker_fee': self.fees.spot_maker_fee,
                'spot_taker_fee': self.fees.spot_taker_fee,
                'futures_maker_fee': self.fees.futures_maker_fee,
                'futures_taker_fee': self.fees.futures_taker_fee
            },
            'database': {
                'path': self.database_path
            },
            'logging': {
                'level': self.log_level,
                'file': self.log_file
            }
        }

        with open(path, 'w') as f:
            yaml.dump(data, f, default_flow_style=False)
