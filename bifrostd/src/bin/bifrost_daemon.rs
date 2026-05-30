use bifrostd::store::MemoryTierConfig;
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
    #[arg(long, default_value_t = 0)]
    memory_tier_bytes: u64,
    #[arg(long, default_value_t = false, action = clap::ArgAction::Set)]
    memory_tier_cache_payloads: bool,
    #[arg(long)]
    memory_tier_max_object_bytes: Option<u64>,
}

#[tokio::main]
async fn main() {
    let args = Args::parse();
    if let Err(error) = serve(ServerConfig {
        listen: args.listen,
        spool_root: args.spool,
        trace_jsonl: args.trace_jsonl,
        memory_tier: MemoryTierConfig {
            capacity_bytes: args.memory_tier_bytes,
            cache_payloads: args.memory_tier_cache_payloads,
            max_object_bytes: args.memory_tier_max_object_bytes,
        },
    })
    .await
    {
        eprintln!("bifrost-daemon: error: {error:#}");
        std::process::exit(1);
    }
}
