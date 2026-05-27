use clap::Parser;
use std::path::PathBuf;

#[derive(Debug, Parser)]
#[command(name = "bifrost_daemon")]
#[command(about = "BIFROST Phase 2 local daemon placeholder")]
struct Args {
    #[arg(long, default_value = "127.0.0.1:7420")]
    listen: String,
    #[arg(long)]
    spool: PathBuf,
}

fn main() {
    let args = Args::parse();
    println!(
        "bifrost_daemon config: listen={} spool={}",
        args.listen,
        args.spool.display()
    );
}
