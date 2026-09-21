"""Optional real-source smoke check for the first AxData core data loop.

The command is deliberately inert by default. Pass ``--run`` or set
``AXDATA_RUN_REAL_SMOKE=1`` to make real source requests.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

import pandas as pd

from axdata_core.downloader_engine import DownloadMetadataWriter, DownloadQualityChecker, DownloadWriter
from axdata_core.plugin_config import load_plugin_config, save_plugin_config
from axdata_core.provider_catalog import build_builtin_provider_registry
from axdata_core.quality import validate_table
from axdata_core.query import query_table
from axdata_core.schema import get_schema
from axdata_core.source_errors import (
    SourceAdapterNotFound,
    SourceInterfaceNotFound,
    SourceRequestValidationError,
    SourceUnavailableError,
)
from axdata_core.source_request import request_interface
from axdata_core.storage import core_table_partition_path
from axdata_core.tdx_plugin_required import TDX_PLUGIN_REQUIRED_MESSAGE


REAL_SMOKE_ENV = "AXDATA_RUN_REAL_SMOKE"
TDX_PROVIDER_ID = "axdata.source.tdx_external"


@dataclass(frozen=True)
class SmokeSpec:
    label: str
    core_table: str | None
    interface_name: str
    provider_note: str
    params_factory: Callable[[argparse.Namespace], dict[str, Any]]
    fields: tuple[str, ...] | None
    transform: Callable[[Sequence[Mapping[str, Any]], argparse.Namespace], pd.DataFrame]
    primary_key: tuple[str, ...] = ()
    default_selected: bool = True
    plugin_hint: str = ""


@dataclass(frozen=True)
class SmokeContext:
    enabled: bool
    run_id: str
    output_root: Path
    data_root: Path
    snapshot_root: Path
    metadata_root: Path
    sample_rows: int


def _stock_basic_params(args: argparse.Namespace) -> dict[str, Any]:
    return {"exchange": args.stock_basic_exchange, "code": args.code}


def _trade_cal_params(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "start_date": args.trade_start_date,
        "end_date": args.trade_end_date,
    }


def _tdx_code_params(args: argparse.Namespace) -> dict[str, Any]:
    return {"code": args.code}


def _historical_list_params(args: argparse.Namespace) -> dict[str, Any]:
    return {"trade_date": args.trade_start_date}


def _cninfo_announcements_params(args: argparse.Namespace) -> dict[str, Any]:
    return {"code": args.code, "limit": max(1, int(args.sample_rows))}


def _cninfo_announcement_detail_params(args: argparse.Namespace) -> dict[str, Any]:
    return {"url": args.announcement_url}


def _tencent_snapshot_params(args: argparse.Namespace) -> dict[str, Any]:
    return {"code": args.code}


def _eastmoney_dragon_tiger_params(args: argparse.Namespace) -> dict[str, Any]:
    return {"trade_date": args.trade_start_date, "limit": max(1, int(args.sample_rows))}


def _eastmoney_code_limited_params(args: argparse.Namespace) -> dict[str, Any]:
    return {"code": args.code, "limit": max(1, int(args.sample_rows))}


def _sina_financial_statement_params(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "code": args.code,
        "statement_type": "income",
        "year": 2024,
        "limit": max(1, int(args.sample_rows)),
    }


def _transform_stock_basic_exchange(
    records: Sequence[Mapping[str, Any]],
    args: argparse.Namespace,
) -> pd.DataFrame:
    return _schema_frame("stock_basic_exchange", records)


def _transform_trade_cal(
    records: Sequence[Mapping[str, Any]],
    args: argparse.Namespace,
) -> pd.DataFrame:
    rows = []
    for row in records:
        rows.append(
            {
                "exchange": "SZSE",
                "cal_date": row.get("cal_date"),
                "is_open": _int_flag(row.get("is_open")),
                "pretrade_date": row.get("pretrade_date"),
            }
        )
    return _schema_frame("trade_cal", rows)


def _transform_daily(
    records: Sequence[Mapping[str, Any]],
    args: argparse.Namespace,
) -> pd.DataFrame:
    rows = []
    for row in records:
        trade_date = _trade_date_from_record(row)
        rows.append(
            {
                "ts_code": row.get("ts_code") or row.get("instrument_id"),
                "trade_date": trade_date,
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "close": row.get("close"),
                "pre_close": row.get("pre_close"),
                "change": row.get("change"),
                "pct_chg": row.get("pct_chg"),
                "vol": row.get("vol", row.get("volume")),
                "amount": row.get("amount"),
            }
        )
    return _schema_frame("daily", rows)


def _transform_adj_factor(
    records: Sequence[Mapping[str, Any]],
    args: argparse.Namespace,
) -> pd.DataFrame:
    rows = []
    for row in records:
        rows.append(
            {
                "ts_code": row.get("ts_code") or row.get("instrument_id"),
                "trade_date": row.get("trade_date") or _trade_date_from_record(row),
                "adj_factor": row.get("adj_factor"),
            }
        )
    return _schema_frame("adj_factor", rows)


def _source_snapshot_frame(
    records: Sequence[Mapping[str, Any]],
    args: argparse.Namespace,
) -> pd.DataFrame:
    return pd.DataFrame.from_records([dict(row) for row in records])


SMOKE_SPECS: tuple[SmokeSpec, ...] = (
    SmokeSpec(
        label="stock_basic",
        core_table="stock_basic_exchange",
        interface_name="stock_basic_info_exchange",
        provider_note="exchange built-in source",
        params_factory=_stock_basic_params,
        fields=None,
        transform=_transform_stock_basic_exchange,
    ),
    SmokeSpec(
        label="trade_cal",
        core_table="trade_cal",
        interface_name="stock_trade_calendar_exchange",
        provider_note="exchange built-in source",
        params_factory=_trade_cal_params,
        fields=("cal_date", "is_open", "pretrade_date"),
        transform=_transform_trade_cal,
    ),
    SmokeSpec(
        label="daily",
        core_table="daily",
        interface_name="stock_kline_daily_tdx",
        provider_note="TDX provider plugin",
        params_factory=_tdx_code_params,
        fields=None,
        transform=_transform_daily,
        plugin_hint=TDX_PLUGIN_REQUIRED_MESSAGE,
    ),
    SmokeSpec(
        label="adj_factor",
        core_table="adj_factor",
        interface_name="stock_adj_factor_tdx",
        provider_note="TDX provider plugin",
        params_factory=_tdx_code_params,
        fields=None,
        transform=_transform_adj_factor,
        plugin_hint=TDX_PLUGIN_REQUIRED_MESSAGE,
        default_selected=False,
    ),
    SmokeSpec(
        label="stock_historical_list",
        core_table=None,
        interface_name="stock_historical_list_exchange",
        provider_note="exchange built-in source",
        params_factory=_historical_list_params,
        fields=None,
        transform=_source_snapshot_frame,
        primary_key=("trade_date", "instrument_id"),
        default_selected=False,
    ),
    SmokeSpec(
        label="cninfo_announcements",
        core_table=None,
        interface_name="cninfo_announcements",
        provider_note="cninfo built-in source",
        params_factory=_cninfo_announcements_params,
        fields=None,
        transform=_source_snapshot_frame,
        primary_key=("instrument_id", "announcement_id"),
        default_selected=False,
    ),
    SmokeSpec(
        label="cninfo_announcement_detail",
        core_table=None,
        interface_name="cninfo_announcement_detail",
        provider_note="cninfo built-in source",
        params_factory=_cninfo_announcement_detail_params,
        fields=None,
        transform=_source_snapshot_frame,
        primary_key=("download_url",),
        default_selected=False,
    ),
    SmokeSpec(
        label="tencent_realtime_snapshot",
        core_table=None,
        interface_name="tencent_realtime_snapshot",
        provider_note="tencent built-in source",
        params_factory=_tencent_snapshot_params,
        fields=None,
        transform=_source_snapshot_frame,
        primary_key=("instrument_id",),
        default_selected=False,
    ),
    SmokeSpec(
        label="eastmoney_dragon_tiger",
        core_table=None,
        interface_name="eastmoney_dragon_tiger_daily",
        provider_note="eastmoney built-in source",
        params_factory=_eastmoney_dragon_tiger_params,
        fields=None,
        transform=_source_snapshot_frame,
        primary_key=("trade_date", "instrument_id", "reason"),
        default_selected=False,
    ),
    SmokeSpec(
        label="eastmoney_margin_trading",
        core_table=None,
        interface_name="eastmoney_margin_trading",
        provider_note="eastmoney built-in source",
        params_factory=_eastmoney_code_limited_params,
        fields=None,
        transform=_source_snapshot_frame,
        primary_key=("trade_date", "instrument_id"),
        default_selected=False,
    ),
    SmokeSpec(
        label="eastmoney_research_reports",
        core_table=None,
        interface_name="eastmoney_research_reports",
        provider_note="eastmoney built-in source",
        params_factory=_eastmoney_code_limited_params,
        fields=None,
        transform=_source_snapshot_frame,
        primary_key=("report_id",),
        default_selected=False,
    ),
    SmokeSpec(
        label="sina_financial_statement",
        core_table=None,
        interface_name="sina_financial_statement",
        provider_note="sina built-in source",
        params_factory=_sina_financial_statement_params,
        fields=None,
        transform=_source_snapshot_frame,
        primary_key=("statement_type", "instrument_id", "report_date", "item_name"),
        default_selected=False,
    ),
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Optional real-source smoke check for stock_basic, trade_cal, daily, and explicit source targets.",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help=f"Actually request real sources. Default is dry skip unless {REAL_SMOKE_ENV}=1.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Smoke artifact root. Defaults to a new temp directory.",
    )
    parser.add_argument(
        "--code",
        default="000001.SZ",
        help="Single stock code for stock_basic, daily, and explicit stock source samples.",
    )
    parser.add_argument(
        "--stock-basic-exchange",
        default="SZSE",
        help="Exchange filter for stock_basic_info_exchange.",
    )
    parser.add_argument(
        "--trade-start-date",
        default="20260617",
        help="Start date for the trade_cal sample, YYYYMMDD.",
    )
    parser.add_argument(
        "--trade-end-date",
        default="20260622",
        help="End date for the trade_cal sample, YYYYMMDD.",
    )
    parser.add_argument(
        "--sample-rows",
        type=int,
        default=5,
        help="Maximum rows written per core table after source retrieval.",
    )
    parser.add_argument(
        "--announcement-url",
        default="https://static.cninfo.com.cn/finalpage/2024-01-23/1218968511.PDF",
        help="Cninfo PDF URL for the cninfo_announcement_detail source-only smoke target.",
    )
    parser.add_argument(
        "--enable-provider",
        action="append",
        default=[],
        help=(
            "Enable one provider id in the smoke temp config before discovery. "
            "Repeatable; useful for axdata.source.tdx_external."
        ),
    )
    parser.add_argument(
        "--interfaces",
        nargs="+",
        default=None,
        help=(
            "Limit smoke targets by label, core table, or interface name. "
            "Examples: daily adj_factor, stock_basic_exchange, stock_kline_daily_tdx, "
            "tencent_realtime_snapshot."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the machine-readable summary JSON.",
    )
    args = parser.parse_args(argv)
    args.interfaces = _normalize_interface_filters(args.interfaces, parser)
    return args


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    context = _build_context(args)
    if context.enabled:
        _enable_requested_providers(args.enable_provider, context.data_root)
    registry = build_builtin_provider_registry(data_root=context.data_root)
    snapshot = registry.snapshot()
    downloader_map = _downloader_map(context.data_root)
    collector_names = sorted(snapshot.collectors)

    results = []
    for spec in _selected_specs(args.interfaces):
        audit = _audit_spec(spec, snapshot, downloader_map, collector_names)
        if not context.enabled:
            results.append(
                _skip_result(
                    spec,
                    audit,
                    reason=f"real source smoke disabled; pass --run or set {REAL_SMOKE_ENV}=1",
                )
            )
            continue
        if not audit["registered"]:
            results.append(_skip_result(spec, audit, reason=_missing_interface_reason(spec, audit)))
            continue
        results.append(_run_one_spec(spec, args, context, audit))

    summary = {
        "run_id": context.run_id,
        "enabled": context.enabled,
        "output_root": str(context.output_root),
        "data_root": str(context.data_root),
        "summary": _status_counts(results),
        "results": results,
    }
    if context.enabled:
        summary_path = context.metadata_root / "summary.json"
        DownloadMetadataWriter().write_text_atomic(
            summary_path,
            json.dumps(_jsonable(summary), ensure_ascii=False, indent=2) + "\n",
        )
        summary["summary_path"] = str(summary_path)
    return summary


def _build_context(args: argparse.Namespace) -> SmokeContext:
    enabled = bool(args.run or _env_enabled(REAL_SMOKE_ENV))
    output_root = args.output_dir
    if output_root is None:
        output_root = Path(tempfile.mkdtemp(prefix="axdata-real-source-smoke-"))
    output_root = output_root.expanduser().resolve()
    run_id = f"real_smoke_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}"
    data_root = output_root / "data"
    return SmokeContext(
        enabled=enabled,
        run_id=run_id,
        output_root=output_root,
        data_root=data_root,
        snapshot_root=output_root / "snapshots" / run_id,
        metadata_root=output_root / "metadata" / "real_source_smoke" / run_id,
        sample_rows=max(1, int(args.sample_rows)),
    )


def _env_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _downloader_map(data_root: Path) -> dict[str, dict[str, Any]]:
    try:
        from axdata_core.downloaders import list_downloader_profiles

        return {row["interface_name"]: dict(row) for row in list_downloader_profiles(data_root=data_root)}
    except Exception:
        return {}


def _enable_requested_providers(provider_ids: Sequence[str], data_root: Path) -> None:
    config = load_plugin_config(data_root=data_root)
    changed = False
    for provider_id in provider_ids:
        clean = str(provider_id or "").strip()
        if not clean:
            continue
        config = config.enable(clean)
        changed = True
    if changed:
        save_plugin_config(config, data_root=data_root)


def _normalize_interface_filters(
    values: Sequence[str] | None,
    parser: argparse.ArgumentParser,
) -> tuple[str, ...] | None:
    if not values:
        return None
    lookup: dict[str, str] = {}
    for spec in SMOKE_SPECS:
        keys = {spec.label, spec.interface_name}
        if spec.core_table:
            keys.add(spec.core_table)
        for key in keys:
            lookup[key] = spec.label
    selected: list[str] = []
    unknown: list[str] = []
    for raw_value in values:
        for token in str(raw_value).split(","):
            clean = token.strip()
            if not clean:
                continue
            label = lookup.get(clean)
            if label is None:
                unknown.append(clean)
                continue
            if label not in selected:
                selected.append(label)
    if unknown:
        parser.error(
            "unknown --interfaces value(s): "
            + ", ".join(unknown)
            + ". Known values: "
            + ", ".join(sorted(lookup))
        )
    return tuple(selected) if selected else None


def _selected_specs(labels: Sequence[str] | None) -> tuple[SmokeSpec, ...]:
    if not labels:
        return tuple(spec for spec in SMOKE_SPECS if spec.default_selected)
    selected = set(labels)
    return tuple(spec for spec in SMOKE_SPECS if spec.label in selected)


def _audit_spec(
    spec: SmokeSpec,
    snapshot: Any,
    downloader_map: Mapping[str, Mapping[str, Any]],
    collector_names: Sequence[str],
) -> dict[str, Any]:
    route = snapshot.interfaces.get(spec.interface_name)
    provider = None if route is None else snapshot.providers.get(route.provider_id)
    declared_provider = provider or _provider_declaring_interface(snapshot, spec.interface_name)
    downloader = downloader_map.get(spec.interface_name)
    collection = None if route is None else route.interface.collection.to_dict()
    status = "source_request_only"
    if route is None:
        status = "missing"
    elif downloader is not None:
        status = "downloader_available"
    return {
        "label": spec.label,
        "core_table": spec.core_table,
        "target_kind": "core_table" if spec.core_table else "source_snapshot",
        "interface_name": spec.interface_name,
        "provider_note": spec.provider_note,
        "registered": route is not None,
        "discovered": declared_provider is not None,
        "provider_id": None if declared_provider is None else declared_provider.provider_id,
        "provider_status": None if declared_provider is None else declared_provider.status,
        "provider_enabled": None if declared_provider is None else getattr(declared_provider, "enabled", None),
        "provider_error": None if declared_provider is None else getattr(declared_provider, "error", ""),
        "resolved_route": route is not None,
        "collection": collection,
        "downloader_available": downloader is not None,
        "downloader_profile": None if downloader is None else downloader.get("manifest_downloader_name"),
        "collector_names": list(collector_names),
        "status": status,
    }


def _provider_declaring_interface(snapshot: Any, interface_name: str) -> Any | None:
    for provider in snapshot.providers.values():
        for interface in provider.manifest.interfaces:
            if interface.name == interface_name:
                return provider
    return None


def _missing_interface_reason(spec: SmokeSpec, audit: Mapping[str, Any]) -> str:
    if spec.interface_name.endswith("_tdx"):
        status = str(audit.get("provider_status") or "").strip()
        provider_error = str(audit.get("provider_error") or "").strip()
        if not audit.get("discovered"):
            return spec.plugin_hint or TDX_PLUGIN_REQUIRED_MESSAGE
        if status == "disabled":
            return "TDX 插件已安装但未启用，请启用 TDX 插件。"
        if status in {"failed", "incompatible", "conflict"}:
            suffix = f"：{provider_error}" if provider_error else ""
            return f"TDX 插件已发现但状态为 {status}{suffix}"
        return f"TDX 插件已发现但接口 {spec.interface_name!r} 未注册。"
    return f"source interface {spec.interface_name!r} is not registered"


def _skip_result(spec: SmokeSpec, audit: Mapping[str, Any], *, reason: str) -> dict[str, Any]:
    return {
        "label": spec.label,
        "core_table": spec.core_table,
        "target_kind": "core_table" if spec.core_table else "source_snapshot",
        "interface_name": spec.interface_name,
        "status": "skip",
        "reason": reason,
        "audit": dict(audit),
    }


def _run_one_spec(
    spec: SmokeSpec,
    args: argparse.Namespace,
    context: SmokeContext,
    audit: Mapping[str, Any],
) -> dict[str, Any]:
    params = spec.params_factory(args)
    requested_fields = list(spec.fields) if spec.fields is not None else None
    started_at = datetime.now(timezone.utc)
    try:
        source_result = request_interface(
            spec.interface_name,
            params=params,
            fields=requested_fields,
            persist=False,
            data_root=context.data_root,
        )
        source_records = [dict(row) for row in source_result.records]
        source_frame = pd.DataFrame.from_records(source_records)
        if spec.core_table is None:
            result = _source_only_result(
                spec,
                args,
                context,
                audit,
                params=params,
                requested_fields=requested_fields,
                source_result=source_result,
                source_frame=source_frame,
                started_at=started_at,
            )
            metadata_path = context.metadata_root / f"{spec.label}.json"
            DownloadMetadataWriter().write_text_atomic(
                metadata_path,
                json.dumps(_jsonable(result), ensure_ascii=False, indent=2) + "\n",
            )
            result.setdefault("output_paths", {})
            result["output_paths"]["metadata_json"] = str(metadata_path)
            return result
        core_frame = spec.transform(source_records, args)
        source_row_count = len(core_frame)
        core_frame = core_frame.head(context.sample_rows).copy()
        if core_frame.empty:
            raise RuntimeError(f"{spec.interface_name} returned no rows after core transform.")
        quality = _quality_summary(spec.core_table, core_frame)
        output_paths = _write_outputs(spec, context, source_frame, core_frame)
        query_result = _query_written_core(spec.core_table, core_frame, context.data_root)
        passed = (
            quality["row_count"] == "pass"
            and quality["schema"] == "pass"
            and quality["primary_key"] == "pass"
            and int(query_result["row_count"]) > 0
        )
        finished_at = datetime.now(timezone.utc)
        result = {
            "label": spec.label,
            "core_table": spec.core_table,
            "interface_name": spec.interface_name,
            "status": "pass" if passed else "fail",
            "params": params,
            "fields": requested_fields,
            "source_row_count": source_row_count,
            "row_count": len(core_frame),
            "quality": quality,
            "query": query_result,
            "output_paths": output_paths,
            "source_meta": dict(source_result.meta),
            "audit": dict(audit),
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
        }
    except (SourceAdapterNotFound, SourceInterfaceNotFound) as exc:
        return _skip_result(spec, audit, reason=str(exc))
    except SourceUnavailableError as exc:
        if TDX_PLUGIN_REQUIRED_MESSAGE in str(exc):
            return _skip_result(spec, audit, reason=TDX_PLUGIN_REQUIRED_MESSAGE)
        result = _failed_result(spec, audit, params=params, exc=exc, started_at=started_at)
    except SourceRequestValidationError as exc:
        result = _failed_result(spec, audit, params=params, exc=exc, started_at=started_at)
    except Exception as exc:
        result = _failed_result(spec, audit, params=params, exc=exc, started_at=started_at)

    metadata_path = context.metadata_root / f"{spec.label}.json"
    DownloadMetadataWriter().write_text_atomic(
        metadata_path,
        json.dumps(_jsonable(result), ensure_ascii=False, indent=2) + "\n",
    )
    result.setdefault("output_paths", {})
    result["output_paths"]["metadata_json"] = str(metadata_path)
    return result


def _failed_result(
    spec: SmokeSpec,
    audit: Mapping[str, Any],
    *,
    params: Mapping[str, Any],
    exc: Exception,
    started_at: datetime,
) -> dict[str, Any]:
    return {
        "label": spec.label,
        "core_table": spec.core_table,
        "target_kind": "core_table" if spec.core_table else "source_snapshot",
        "interface_name": spec.interface_name,
        "status": "fail",
        "params": dict(params),
        "error_type": type(exc).__name__,
        "error": str(exc),
        "audit": dict(audit),
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }


def _source_only_result(
    spec: SmokeSpec,
    args: argparse.Namespace,
    context: SmokeContext,
    audit: Mapping[str, Any],
    *,
    params: Mapping[str, Any],
    requested_fields: list[str] | None,
    source_result: Any,
    source_frame: pd.DataFrame,
    started_at: datetime,
) -> dict[str, Any]:
    frame = spec.transform([dict(row) for row in source_result.records], args).head(context.sample_rows).copy()
    if frame.empty:
        raise RuntimeError(f"{spec.interface_name} returned no rows.")
    quality = _source_quality_summary(frame, primary_key=spec.primary_key)
    output_paths = _write_source_outputs(spec, context, frame)
    query_result = _query_source_snapshot(output_paths["source_parquet"])
    passed = (
        quality["row_count"] == "pass"
        and quality["schema"] == "pass"
        and quality.get("primary_key", "pass") == "pass"
        and int(query_result["row_count"]) > 0
    )
    finished_at = datetime.now(timezone.utc)
    return {
        "label": spec.label,
        "core_table": None,
        "target_kind": "source_snapshot",
        "interface_name": spec.interface_name,
        "status": "pass" if passed else "fail",
        "params": dict(params),
        "fields": requested_fields,
        "source_row_count": len(source_frame),
        "row_count": len(frame),
        "quality": quality,
        "query": query_result,
        "output_paths": output_paths,
        "source_meta": dict(source_result.meta),
        "audit": dict(audit),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
    }


def _schema_frame(table: str, records: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    schema = get_schema(table)
    frame = pd.DataFrame.from_records([dict(row) for row in records])
    if frame.empty:
        return pd.DataFrame(columns=list(schema.field_names))
    for field in schema.field_names:
        if field not in frame.columns:
            frame[field] = None
    return frame[list(schema.field_names)]


def _trade_date_from_record(row: Mapping[str, Any]) -> str | None:
    for key in ("trade_date", "trade_time", "datetime", "date"):
        value = row.get(key)
        if value in (None, ""):
            continue
        digits = re.sub(r"\D", "", str(value))
        if len(digits) >= 8:
            return digits[:8]
    return None


def _int_flag(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y"}:
            return 1
        if text in {"0", "false", "no", "n"}:
            return 0
    return 1 if bool(value) else 0


def _quality_summary(table: str, frame: pd.DataFrame) -> dict[str, Any]:
    schema = get_schema(table)
    checker = DownloadQualityChecker()
    numeric_columns = [
        field.name
        for field in schema.fields
        if field.dtype == "float64" and field.name not in {"change", "pct_chg"}
    ]
    built_in = checker.evaluate(
        frame,
        primary_key=schema.primary_key,
        required_columns=schema.required_fields,
        expected_columns=schema.field_names,
        date_field=schema.date_field,
        datetime_field=schema.datetime_field,
        numeric_positive_columns=numeric_columns,
        field_mappings=dict(schema.provider_field_mappings),
    )
    issues = validate_table(table, frame)
    issue_checks = {issue.check for issue in issues}
    quality = dict(built_in)
    quality.update(
        {
            "row_count": "pass" if len(frame) > 0 else "fail",
            "schema": "fail" if "missing_required" in issue_checks else built_in.get("schema", "pass"),
            "primary_key": "fail"
            if "primary_key_duplicates" in issue_checks
            else built_in.get("primary_key", "pass"),
        }
    )
    quality["issues"] = [
        {
            "check": issue.check,
            "table": issue.table,
            "message": issue.message,
            "rows": issue.rows,
        }
        for issue in issues
    ]
    if issues and quality.get("quality_status") == "ok":
        quality["quality_status"] = "error"
    return quality


def _source_quality_summary(frame: pd.DataFrame, *, primary_key: tuple[str, ...]) -> dict[str, Any]:
    checker = DownloadQualityChecker()
    rules = ["schema", "row_count"]
    if primary_key:
        rules.append("primary_key")
    quality = checker.evaluate(frame, primary_key=primary_key or "__axdata_row__", rules=rules)
    return quality


def _write_outputs(
    spec: SmokeSpec,
    context: SmokeContext,
    source_frame: pd.DataFrame,
    core_frame: pd.DataFrame,
) -> dict[str, str]:
    writer = DownloadWriter()
    file_stem = f"{spec.label}_{context.run_id}"
    source_path = context.snapshot_root / spec.interface_name / "parquet" / f"{file_stem}.parquet"
    core_path = _core_output_path(spec.core_table, core_frame, context, file_stem)
    source_path.parent.mkdir(parents=True, exist_ok=True)
    core_path.parent.mkdir(parents=True, exist_ok=True)
    writer.write_frame_atomic(source_frame, source_path, "parquet")
    writer.write_frame_atomic(core_frame, core_path, "parquet")
    return {
        "source_parquet": str(source_path),
        "core_parquet": str(core_path),
    }


def _write_source_outputs(
    spec: SmokeSpec,
    context: SmokeContext,
    frame: pd.DataFrame,
) -> dict[str, str]:
    writer = DownloadWriter()
    file_stem = f"{spec.label}_{context.run_id}"
    root = context.snapshot_root / spec.interface_name
    paths = {
        "source_parquet": root / "parquet" / f"{file_stem}.parquet",
        "source_csv": root / "csv" / f"{file_stem}.csv",
        "source_duckdb": root / "duckdb" / f"{file_stem}.duckdb",
    }
    for output_format, key in (("parquet", "source_parquet"), ("csv", "source_csv"), ("duckdb", "source_duckdb")):
        paths[key].parent.mkdir(parents=True, exist_ok=True)
        writer.write_frame_atomic(frame, paths[key], output_format)
    return {key: str(value) for key, value in paths.items()}


def _core_output_path(table: str, frame: pd.DataFrame, context: SmokeContext, file_stem: str) -> Path:
    schema = get_schema(table)
    root = core_table_partition_path(table, context.data_root)
    partition_parts = [f"smoke_run_id={context.run_id}"]
    if schema.date_field and schema.date_field in frame.columns:
        values = sorted({str(value).replace("-", "") for value in frame[schema.date_field].dropna().unique()})
        if len(values) == 1:
            partition_parts.insert(0, f"{schema.date_field}={values[0]}")
    return root.joinpath(*partition_parts, f"{file_stem}.parquet")


def _query_written_core(table: str, frame: pd.DataFrame, data_root: Path) -> dict[str, Any]:
    schema = get_schema(table)
    first = frame.iloc[0].to_dict()
    filters = {}
    for field in schema.primary_key:
        value = first.get(field)
        if value is not None and not pd.isna(value):
            filters[field] = _scalar(value)
    fields = list(schema.primary_key)
    if schema.date_field and schema.date_field not in fields:
        fields.append(schema.date_field)
    result = query_table(
        table,
        root=data_root,
        filters=filters,
        fields=fields,
        limit=5,
    )
    return {
        "row_count": int(len(result)),
        "filters": filters,
        "fields": fields,
    }


def _query_source_snapshot(parquet_path: str) -> dict[str, Any]:
    import duckdb

    with duckdb.connect(database=":memory:") as conn:
        row_count = conn.execute(
            "SELECT COUNT(*) FROM read_parquet(?)",
            [parquet_path],
        ).fetchone()[0]
    return {
        "row_count": int(row_count),
        "source_parquet": parquet_path,
    }


def _status_counts(results: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = {"pass": 0, "fail": 0, "skip": 0}
    for row in results:
        status = str(row.get("status") or "")
        if status in counts:
            counts[status] += 1
    return counts


def _scalar(value: Any) -> Any:
    if hasattr(value, "item"):
        try:
            return value.item()
        except ValueError:
            return value
    return value


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if _is_missing_scalar(value):
        return None
    return _scalar(value)


def _is_missing_scalar(value: Any) -> bool:
    if isinstance(value, (Mapping, list, tuple, set, str, bytes)):
        return False
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        return False
    try:
        return bool(missing)
    except ValueError:
        return False


def print_human(summary: Mapping[str, Any]) -> None:
    counts = summary["summary"]
    print("AxData real-source smoke")
    print(f"run_id: {summary['run_id']}")
    print(f"enabled: {summary['enabled']}")
    print(f"output_root: {summary['output_root']}")
    print(f"summary: pass={counts['pass']} fail={counts['fail']} skip={counts['skip']}")
    for result in summary["results"]:
        status = str(result["status"]).upper()
        label = result["label"]
        interface = result["interface_name"]
        row_count = result.get("row_count", "-")
        reason = result.get("reason") or result.get("error") or ""
        suffix = f" rows={row_count}" if row_count != "-" else ""
        if reason:
            suffix += f" reason={reason}"
        print(f"- {status} {label} via {interface}{suffix}")
    if "summary_path" in summary:
        print(f"summary_path: {summary['summary_path']}")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    summary = run_smoke(args)
    if args.json:
        print(json.dumps(_jsonable(summary), ensure_ascii=False, indent=2))
    else:
        print_human(summary)
    return 1 if summary["summary"]["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
