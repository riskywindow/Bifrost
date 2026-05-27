use bifrostd::transport::{serve, ServerConfig};
use clap::Parser;
use std::path::PathBuf;

#[derive(Debug, Parser)]
#[command(name = "bifrost-daemon")]
#[command(about = "BIFROST Phase 2 local daemon placeholder")]
struct Args {
    #[arg(long, default_value = "127.0.0.1:7420")]
    listen: String,
    #[arg(long)]
    spool: PathBuf,
    #[arg(long)]
    trace_jsonl: Option<PathBuf>,
}

#[tokio::main]
async fn main() {
    let args = Args::parse();
    if let Err(error) = serve(ServerConfig {
        listen: args.listen,
        spool_root: args.spool,
        trace_jsonl: args.trace_jsonl,
    })
    .await
    {
        eprintln!("bifrost-daemon: error: {error:#}");
        std::process::exit(1);
    }
}
