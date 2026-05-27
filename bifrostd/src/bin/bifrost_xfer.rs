use bifrostd::transport::{get_object, has_object, put_object, DEFAULT_CHUNK_SIZE};
use clap::{Parser, Subcommand};
use serde_json::json;
use serde_json::Value;
use std::fs;
use std::path::PathBuf;

#[derive(Debug, Parser)]
#[command(name = "bifrost-xfer")]
#[command(about = "BIFROST Phase 2 transfer client placeholder")]
struct Args {
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    Put {
        #[arg(long, default_value = "127.0.0.1:7420")]
        endpoint: String,
        #[arg(long)]
        meta: PathBuf,
        #[arg(long)]
        payload: PathBuf,
        #[arg(long, default_value_t = DEFAULT_CHUNK_SIZE)]
        chunk_size: usize,
        #[arg(long)]
        target: Option<PathBuf>,
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
    },
    Has {
        #[arg(long, default_value = "127.0.0.1:7420")]
        endpoint: String,
        #[arg(long)]
        object_id: String,
        #[arg(long)]
        json: bool,
    },
}

#[tokio::main]
async fn main() {
    let args = Args::parse();
    match args.command {
        Command::Put {
            endpoint,
            meta,
            payload,
            chunk_size,
            target,
        } => {
            let result = run_put(&endpoint, meta, payload, chunk_size, target).await;
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
        } => {
            let result = run_get(&endpoint, &object_id, out, chunk_size).await;
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
            json,
        } => {
            let result = run_has(&endpoint, &object_id, json).await;
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
    meta: PathBuf,
    payload: PathBuf,
    chunk_size: usize,
    target: Option<PathBuf>,
) -> anyhow::Result<bool> {
    let metadata_bytes = fs::read(meta)?;
    let payload = fs::read(payload)?;
    let target_profile: Option<Value> = target
        .map(|path| -> anyhow::Result<Value> { Ok(serde_json::from_slice(&fs::read(path)?)?) })
        .transpose()?;

    let outcome = put_object(
        endpoint,
        metadata_bytes,
        payload,
        chunk_size,
        target_profile,
    )
    .await?;
    if outcome.accepted {
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
) -> anyhow::Result<bool> {
    let outcome = get_object(endpoint, object_id, chunk_size).await?;
    if !outcome.found {
        println!(
            "miss object_id={} reason={}",
            outcome.object_id, outcome.reason
        );
        return Ok(false);
    }

    fs::create_dir_all(&out)?;
    fs::write(out.join("meta.json"), &outcome.metadata_bytes)?;
    fs::write(out.join("payload.bin"), &outcome.payload)?;
    println!("fetched object_id={}", outcome.object_id);
    Ok(true)
}
