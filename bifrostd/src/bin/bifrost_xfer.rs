use clap::{Parser, Subcommand};
use std::path::PathBuf;

#[derive(Debug, Parser)]
#[command(name = "bifrost_xfer")]
#[command(about = "BIFROST Phase 2 transfer client placeholder")]
struct Args {
    #[arg(long, default_value = "127.0.0.1:7420")]
    connect: String,
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    Put {
        #[arg(long)]
        meta: PathBuf,
        #[arg(long)]
        payload: PathBuf,
    },
    Get {
        #[arg(long)]
        object_id: String,
        #[arg(long)]
        out: Option<PathBuf>,
    },
    Has {
        #[arg(long)]
        object_id: String,
    },
}

fn main() {
    let args = Args::parse();
    match args.command {
        Command::Put { meta, payload } => {
            println!(
                "put is not implemented in this task: connect={} meta={} payload={}",
                args.connect,
                meta.display(),
                payload.display()
            );
        }
        Command::Get { object_id, out } => {
            let out = out
                .as_ref()
                .map(|path| path.display().to_string())
                .unwrap_or_else(|| "<stdout>".to_string());
            println!(
                "get is not implemented in this task: connect={} object_id={} out={}",
                args.connect, object_id, out
            );
        }
        Command::Has { object_id } => {
            println!(
                "has is not implemented in this task: connect={} object_id={}",
                args.connect, object_id
            );
        }
    }
}
