use bifrostd::transport::{put_object, DEFAULT_CHUNK_SIZE};
use clap::{Parser, Subcommand};
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
        out: Option<PathBuf>,
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
        } => {
            let out = out
                .as_ref()
                .map(|path| path.display().to_string())
                .unwrap_or_else(|| "<stdout>".to_string());
            println!(
                "get is not implemented in this task: endpoint={} object_id={} out={}",
                endpoint, object_id, out
            );
        }
        Command::Has {
            endpoint,
            object_id,
        } => {
            println!(
                "has is not implemented in this task: endpoint={} object_id={}",
                endpoint, object_id
            );
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
