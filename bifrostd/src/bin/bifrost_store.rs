use bifrostd::transport::{
    clear_ttl, evict_store, inspect_store_object, list_store_objects, pin_object,
    quarantine_object, query_store_objects, set_ttl, store_stats, unpin_object, StoreEvictRequest,
    StoreObjectFilter, StoreObjectSummary, StoreOperationOutcome,
};
use clap::{Parser, Subcommand};
use serde_json::json;

#[derive(Debug, Parser)]
#[command(name = "bifrost-store")]
#[command(about = "BIFROST Phase 3 local store client")]
struct Args {
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    List {
        #[arg(long, default_value = "127.0.0.1:7420")]
        endpoint: String,
        #[arg(long)]
        state: Option<String>,
        #[arg(long)]
        model_hash: Option<String>,
        #[arg(long)]
        prefix_hash: Option<String>,
        #[arg(long)]
        limit: Option<i64>,
        #[arg(long)]
        json: bool,
    },
    Inspect {
        #[arg(long, default_value = "127.0.0.1:7420")]
        endpoint: String,
        #[arg(long)]
        object_id: String,
        #[arg(long)]
        json: bool,
    },
    Query {
        #[arg(long, default_value = "127.0.0.1:7420")]
        endpoint: String,
        #[arg(long)]
        model_hash: Option<String>,
        #[arg(long)]
        prefix_hash: Option<String>,
        #[arg(long)]
        engine_name: Option<String>,
        #[arg(long)]
        opaque_engine_key_hash: Option<String>,
        #[arg(long)]
        layer_id: Option<i64>,
        #[arg(long)]
        kv_block_id: Option<i64>,
        #[arg(long)]
        json: bool,
    },
    Stats {
        #[arg(long, default_value = "127.0.0.1:7420")]
        endpoint: String,
        #[arg(long)]
        json: bool,
    },
    Evict {
        #[arg(long, default_value = "127.0.0.1:7420")]
        endpoint: String,
        #[arg(long)]
        policy: String,
        #[arg(long)]
        target_bytes: Option<i64>,
        #[arg(long)]
        max_objects: Option<usize>,
        #[arg(long)]
        dry_run: bool,
        #[arg(long)]
        json: bool,
    },
    Pin {
        #[arg(long, default_value = "127.0.0.1:7420")]
        endpoint: String,
        #[arg(long)]
        object_id: String,
    },
    Unpin {
        #[arg(long, default_value = "127.0.0.1:7420")]
        endpoint: String,
        #[arg(long)]
        object_id: String,
    },
    Ttl {
        #[command(subcommand)]
        command: TtlCommand,
    },
    Quarantine {
        #[arg(long, default_value = "127.0.0.1:7420")]
        endpoint: String,
        #[arg(long)]
        object_id: String,
        #[arg(long)]
        reason: String,
    },
}

#[derive(Debug, Subcommand)]
enum TtlCommand {
    Set {
        #[arg(long, default_value = "127.0.0.1:7420")]
        endpoint: String,
        #[arg(long)]
        object_id: String,
        #[arg(long)]
        expires_at_unix_ms: i64,
    },
    Clear {
        #[arg(long, default_value = "127.0.0.1:7420")]
        endpoint: String,
        #[arg(long)]
        object_id: String,
    },
}

#[tokio::main]
async fn main() {
    let args = Args::parse();
    let result = match args.command {
        Command::List {
            endpoint,
            state,
            model_hash,
            prefix_hash,
            limit,
            json,
        } => {
            run_list(
                &endpoint,
                StoreObjectFilter {
                    state,
                    model_hash,
                    prefix_hash,
                    limit,
                    ..StoreObjectFilter::default()
                },
                json,
            )
            .await
        }
        Command::Inspect {
            endpoint,
            object_id,
            json,
        } => run_inspect(&endpoint, &object_id, json).await,
        Command::Query {
            endpoint,
            model_hash,
            prefix_hash,
            engine_name,
            opaque_engine_key_hash,
            layer_id,
            kv_block_id,
            json,
        } => {
            run_query(
                &endpoint,
                StoreObjectFilter {
                    model_hash,
                    prefix_hash,
                    engine_name,
                    opaque_engine_key_hash,
                    layer_id,
                    kv_block_id,
                    ..StoreObjectFilter::default()
                },
                json,
            )
            .await
        }
        Command::Stats { endpoint, json } => run_stats(&endpoint, json).await,
        Command::Evict {
            endpoint,
            policy,
            target_bytes,
            max_objects,
            dry_run,
            json,
        } => {
            run_evict(
                &endpoint,
                StoreEvictRequest {
                    policy,
                    target_bytes,
                    max_objects,
                    dry_run,
                    now_unix_ms: None,
                },
                json,
            )
            .await
        }
        Command::Pin {
            endpoint,
            object_id,
        } => pin_object(&endpoint, &object_id)
            .await
            .and_then(run_operation),
        Command::Unpin {
            endpoint,
            object_id,
        } => unpin_object(&endpoint, &object_id)
            .await
            .and_then(run_operation),
        Command::Ttl { command } => match command {
            TtlCommand::Set {
                endpoint,
                object_id,
                expires_at_unix_ms,
            } => set_ttl(&endpoint, &object_id, expires_at_unix_ms)
                .await
                .and_then(run_operation),
            TtlCommand::Clear {
                endpoint,
                object_id,
            } => clear_ttl(&endpoint, &object_id)
                .await
                .and_then(run_operation),
        },
        Command::Quarantine {
            endpoint,
            object_id,
            reason,
        } => quarantine_object(&endpoint, &object_id, &reason)
            .await
            .and_then(run_operation),
    };

    match result {
        Ok(code) => std::process::exit(code),
        Err(error) => {
            eprintln!("bifrost-store: error: {error:#}");
            std::process::exit(2);
        }
    }
}

async fn run_list(endpoint: &str, filter: StoreObjectFilter, as_json: bool) -> anyhow::Result<i32> {
    let outcome = list_store_objects(endpoint, filter).await?;
    if !outcome.reason.is_empty() {
        anyhow::bail!(outcome.reason);
    }
    if as_json {
        println!(
            "{}",
            serde_json::to_string(&json!({ "objects": outcome.objects }))?
        );
    } else {
        print_objects(&outcome.objects);
    }
    Ok(0)
}

async fn run_query(
    endpoint: &str,
    filter: StoreObjectFilter,
    as_json: bool,
) -> anyhow::Result<i32> {
    let outcome = query_store_objects(endpoint, filter).await?;
    if !outcome.reason.is_empty() {
        anyhow::bail!(outcome.reason);
    }
    if as_json {
        println!(
            "{}",
            serde_json::to_string(&json!({ "objects": outcome.objects }))?
        );
    } else {
        print_objects(&outcome.objects);
    }
    Ok(if outcome.objects.is_empty() { 1 } else { 0 })
}

async fn run_inspect(endpoint: &str, object_id: &str, as_json: bool) -> anyhow::Result<i32> {
    let outcome = inspect_store_object(endpoint, object_id).await?;
    if as_json {
        println!("{}", serde_json::to_string(&outcome.response)?);
    } else if outcome.found {
        if let Some(object) = outcome.response.object.as_ref() {
            print_object(object);
        }
    } else {
        println!(
            "miss object_id={} reason={}",
            object_id,
            outcome.response.reason.as_deref().unwrap_or("not_found")
        );
    }
    Ok(if outcome.found { 0 } else { 1 })
}

async fn run_stats(endpoint: &str, as_json: bool) -> anyhow::Result<i32> {
    let outcome = store_stats(endpoint).await?;
    if !outcome.reason.is_empty() {
        anyhow::bail!(outcome.reason);
    }
    if as_json {
        println!("{}", serde_json::to_string(&outcome.stats)?);
    } else {
        println!(
            "object_count={} total_logical_bytes={} total_bytes_on_disk={} verified_count={}",
            outcome.stats.object_count,
            outcome.stats.total_logical_bytes,
            outcome.stats.total_bytes_on_disk,
            outcome.stats.verified_count
        );
    }
    Ok(0)
}

async fn run_evict(
    endpoint: &str,
    request: StoreEvictRequest,
    as_json: bool,
) -> anyhow::Result<i32> {
    let outcome = evict_store(endpoint, request).await?;
    if !outcome.reason.is_empty() {
        anyhow::bail!(outcome.reason);
    }
    if as_json {
        println!("{}", serde_json::to_string(&outcome.report)?);
    } else {
        println!(
            "policy={} dry_run={} candidates={} planned_bytes={} evicted={} freed_bytes={} final_bytes_on_disk={} reason={}",
            outcome.report.policy,
            outcome.report.dry_run,
            outcome.report.candidates.len(),
            outcome.report.planned_bytes,
            outcome.report.evicted.len(),
            outcome.report.freed_bytes,
            outcome.report.final_bytes_on_disk,
            outcome.report.reason
        );
        for candidate in &outcome.report.candidates {
            println!(
                "candidate object_id={} bytes_on_disk={} last_accessed_unix_ms={} ttl_expires_at_unix_ms={} eviction_score={}",
                candidate.object_id,
                candidate.bytes_on_disk,
                candidate
                    .last_accessed_unix_ms
                    .map(|value| value.to_string())
                    .unwrap_or_else(|| "-".to_string()),
                candidate
                    .ttl_expires_at_unix_ms
                    .map(|value| value.to_string())
                    .unwrap_or_else(|| "-".to_string()),
                candidate.eviction_score
            );
        }
    }
    Ok(if outcome.report.failures.is_empty() {
        0
    } else {
        1
    })
}

fn run_operation(outcome: StoreOperationOutcome) -> anyhow::Result<i32> {
    if outcome.accepted {
        println!("ok object_id={}", outcome.object_id);
        Ok(0)
    } else {
        println!(
            "error object_id={} reason={}",
            outcome.object_id, outcome.reason
        );
        Ok(1)
    }
}

fn print_objects(objects: &[StoreObjectSummary]) {
    for object in objects {
        print_object(object);
    }
}

fn print_object(object: &StoreObjectSummary) {
    println!(
        "object_id={} object_type={} state={} byte_length={} prefix_hash={} layer_id={} kv_block_id={} pin_count={} last_accessed_unix_ms={}",
        object.object_id,
        object.object_type,
        object.state,
        object.byte_length,
        object.prefix_hash.as_deref().unwrap_or("-"),
        object
            .layer_id
            .map(|value| value.to_string())
            .unwrap_or_else(|| "-".to_string()),
        object
            .kv_block_id
            .map(|value| value.to_string())
            .unwrap_or_else(|| "-".to_string()),
        object.pin_count,
        object
            .last_accessed_unix_ms
            .map(|value| value.to_string())
            .unwrap_or_else(|| "-".to_string())
    );
}
