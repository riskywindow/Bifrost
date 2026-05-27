use anyhow::{Context, Result};
use bifrostd::cache::validate_object;
use clap::Parser;
use serde_json::Value;
use std::path::PathBuf;
use std::process::ExitCode;

#[derive(Debug, Parser)]
#[command(name = "bifrost-kv-validate")]
#[command(about = "Validate a BIFROST Phase 1 KV object fixture")]
struct Args {
    #[arg(long)]
    meta: PathBuf,
    #[arg(long)]
    payload: PathBuf,
    #[arg(long)]
    target: Option<PathBuf>,
    #[arg(long)]
    json: bool,
}

fn main() -> ExitCode {
    match run() {
        Ok(code) => code,
        Err(error) => {
            eprintln!("bifrost-kv-validate: error: {error:#}");
            ExitCode::from(2)
        }
    }
}

fn run() -> Result<ExitCode> {
    let args = Args::parse();
    let metadata = read_json(&args.meta, "metadata")?;
    let payload = std::fs::read(&args.payload)
        .with_context(|| format!("cannot read payload file {}", args.payload.display()))?;
    let target = args
        .target
        .as_ref()
        .map(|path| read_json(path, "target profile"))
        .transpose()?;

    let result = validate_object(&metadata, &payload, target.as_ref());
    if args.json {
        println!("{}", serde_json::to_string(&result)?);
    } else if result.status == "accepted" {
        println!("ACCEPTED");
    } else {
        println!("REJECTED: {}", result.reason_code);
    }

    Ok(if result.status == "accepted" {
        ExitCode::SUCCESS
    } else {
        ExitCode::from(1)
    })
}

fn read_json(path: &PathBuf, label: &str) -> Result<Value> {
    let bytes = std::fs::read(path)
        .with_context(|| format!("cannot read {label} JSON {}", path.display()))?;
    let value = serde_json::from_slice(&bytes)
        .with_context(|| format!("cannot parse {label} JSON {}", path.display()))?;
    Ok(value)
}
