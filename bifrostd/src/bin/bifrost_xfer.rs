use bifrostd::transport::{
    get_object_observed, has_object, put_object_multipath_observed_with_options,
    put_object_observed, ClientTelemetry, MultipathPutOptions, PathSpec, TraceSink,
    TransportMetrics, DEFAULT_CHUNK_SIZE, DEFAULT_CHUNK_TIMEOUT_MS, DEFAULT_MAX_INFLIGHT_PER_PATH,
    DEFAULT_MAX_RETRIES_PER_CHUNK,
};
use clap::{Parser, Subcommand};
use serde_json::json;
use serde_json::Value;
use std::fs;
use std::path::PathBuf;
use std::time::Duration;

#[derive(Debug, Parser)]
#[command(name = "bifrost-xfer")]
#[command(about = "BIFROST Phase 2 transfer client placeholder")]
struct Args {
    #[arg(long, global = true)]
    json: bool,
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    Put {
        #[arg(long, default_value = "127.0.0.1:7420")]
        endpoint: String,
        #[arg(long = "path")]
        paths: Vec<PathSpec>,
        #[arg(long)]
        meta: PathBuf,
        #[arg(long)]
        payload: PathBuf,
        #[arg(long, default_value_t = DEFAULT_CHUNK_SIZE)]
        chunk_size: usize,
        #[arg(long)]
        target: Option<PathBuf>,
        #[arg(long)]
        trace_jsonl: Option<PathBuf>,
        #[arg(long, default_value_t = DEFAULT_CHUNK_TIMEOUT_MS)]
        chunk_timeout_ms: u64,
        #[arg(long, default_value_t = DEFAULT_MAX_RETRIES_PER_CHUNK)]
        max_retries_per_chunk: u32,
        #[arg(long, default_value_t = DEFAULT_MAX_INFLIGHT_PER_PATH)]
        max_inflight_per_path: u64,
    },
    Get {
        #[arg(long, default_value = "127.0.0.1:7420")]
        endpoint: String,
        #[arg(long)]
        object_id: String,
        #[arg(long)]
        out: PathBuf,
        #[arg(long, default_value_t = DEFAULT_CHUNK_SIZE)]
        chunk_size: usize,
        #[arg(long)]
        trace_jsonl: Option<PathBuf>,
    },
    Has {
        #[arg(long, default_value = "127.0.0.1:7420")]
        endpoint: String,
        #[arg(long)]
        object_id: String,
    },
}

#[tokio::main]
async fn main() {
    let args = Args::parse();
    let as_json = args.json;
    match args.command {
        Command::Put {
            endpoint,
            paths,
            meta,
            payload,
            chunk_size,
            target,
            trace_jsonl,
            chunk_timeout_ms,
            max_retries_per_chunk,
            max_inflight_per_path,
        } => {
            let result = run_put(
                &endpoint,
                paths,
                meta,
                payload,
                chunk_size,
                target,
                trace_jsonl,
                MultipathPutOptions {
                    chunk_timeout: Duration::from_millis(chunk_timeout_ms),
                    max_retries_per_chunk,
                    max_inflight_per_path,
                },
                as_json,
            )
            .await;
            match result {
                Ok(true) => std::process::exit(0),
                Ok(false) => std::process::exit(1),
                Err(error) => {
                    eprintln!("bifrost-xfer: error: {error:#}");
                    std::process::exit(2);
                }
            }
        }
        Command::Get {
            endpoint,
            object_id,
            out,
            chunk_size,
            trace_jsonl,
        } => {
            let result =
                run_get(&endpoint, &object_id, out, chunk_size, trace_jsonl, as_json).await;
            match result {
                Ok(true) => std::process::exit(0),
                Ok(false) => std::process::exit(1),
                Err(error) => {
                    eprintln!("bifrost-xfer: error: {error:#}");
                    std::process::exit(2);
                }
            }
        }
        Command::Has {
            endpoint,
            object_id,
        } => {
            let result = run_has(&endpoint, &object_id, as_json).await;
            match result {
                Ok(true) => std::process::exit(0),
                Ok(false) => std::process::exit(1),
                Err(error) => {
                    eprintln!("bifrost-xfer: error: {error:#}");
                    std::process::exit(2);
                }
            }
        }
    }
}

async fn run_put(
    endpoint: &str,
    paths: Vec<PathSpec>,
    meta: PathBuf,
    payload: PathBuf,
    chunk_size: usize,
    target: Option<PathBuf>,
    trace_jsonl: Option<PathBuf>,
    multipath_options: MultipathPutOptions,
    as_json: bool,
) -> anyhow::Result<bool> {
    let metadata_bytes = fs::read(meta)?;
    let payload = fs::read(payload)?;
    let target_profile: Option<Value> = target
        .map(|path| -> anyhow::Result<Value> { Ok(serde_json::from_slice(&fs::read(path)?)?) })
        .transpose()?;
    let telemetry = telemetry(trace_jsonl)?;

    let outcome = if paths.is_empty() {
        put_object_observed(
            endpoint,
            metadata_bytes,
            payload,
            chunk_size,
            target_profile,
            telemetry.clone(),
        )
        .await?
    } else {
        put_object_multipath_observed_with_options(
            paths,
            metadata_bytes,
            payload,
            chunk_size,
            target_profile,
            telemetry.clone(),
            multipath_options,
        )
        .await?
    };
    if as_json {
        println!(
            "{}",
            serde_json::to_string(&json!({
                "accepted": outcome.accepted,
                "object_id": outcome.object_id,
                "reason": outcome.reason,
                "metrics": telemetry.metrics.snapshot(),
            }))?
        );
    } else if outcome.accepted {
        println!("accepted object_id={}", outcome.object_id);
    } else {
        println!(
            "rejected object_id={} reason={}",
            outcome.object_id, outcome.reason
        );
    }
    Ok(outcome.accepted)
}

async fn run_has(endpoint: &str, object_id: &str, as_json: bool) -> anyhow::Result<bool> {
    let outcome = has_object(endpoint, object_id).await?;
    if as_json {
        println!(
            "{}",
            serde_json::to_string(&json!({
                "object_id": outcome.object_id,
                "present": outcome.present,
                "reason": outcome.reason,
            }))?
        );
    } else if outcome.present {
        println!("yes");
    } else {
        println!("no");
    }
    Ok(outcome.present)
}

async fn run_get(
    endpoint: &str,
    object_id: &str,
    out: PathBuf,
    chunk_size: usize,
    trace_jsonl: Option<PathBuf>,
    as_json: bool,
) -> anyhow::Result<bool> {
    let telemetry = telemetry(trace_jsonl)?;
    let outcome = get_object_observed(endpoint, object_id, chunk_size, telemetry.clone()).await?;
    if !outcome.found {
        if as_json {
            println!(
                "{}",
                serde_json::to_string(&json!({
                    "found": false,
                    "object_id": outcome.object_id,
                    "reason": outcome.reason,
                    "metrics": telemetry.metrics.snapshot(),
                }))?
            );
        } else {
            println!(
                "miss object_id={} reason={}",
                outcome.object_id, outcome.reason
            );
        }
        return Ok(false);
    }

    fs::create_dir_all(&out)?;
    fs::write(out.join("meta.json"), &outcome.metadata_bytes)?;
    fs::write(out.join("payload.bin"), &outcome.payload)?;
    if as_json {
        println!(
            "{}",
            serde_json::to_string(&json!({
                "found": true,
                "object_id": outcome.object_id,
                "bytes": outcome.payload.len(),
                "metrics": telemetry.metrics.snapshot(),
            }))?
        );
    } else {
        println!("fetched object_id={}", outcome.object_id);
    }
    Ok(true)
}

fn telemetry(trace_jsonl: Option<PathBuf>) -> anyhow::Result<ClientTelemetry> {
    Ok(ClientTelemetry {
        metrics: TransportMetrics::default(),
        trace: trace_jsonl.map(TraceSink::create).transpose()?,
    })
}
