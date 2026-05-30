use bifrostd::store::ManifestListFilter;
use bifrostd::transport::{
    check_manifest, clear_ttl, create_prefix_manifest, evict_store, inspect_manifest,
    inspect_store_object, list_manifests, list_store_objects, manifest_add_member, manifest_pin,
    manifest_unpin, pin_object, quarantine_object, query_store_objects, set_ttl, store_stats,
    unpin_object, StoreEvictRequest, StoreObjectFilter, StoreObjectSummary, StoreOperationOutcome,
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
    Manifest {
        #[command(subcommand)]
        command: ManifestCommand,
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

#[derive(Debug, Subcommand)]
enum ManifestCommand {
    CreatePrefix {
        #[arg(long, default_value = "127.0.0.1:7420")]
        endpoint: String,
        #[arg(long)]
        prefix_hash: String,
        #[arg(long)]
        model_hash: String,
        #[arg(long)]
        tokenizer_hash: Option<String>,
        #[arg(long)]
        rope_config_hash: Option<String>,
        #[arg(long)]
        token_range_start: i64,
        #[arg(long)]
        token_range_end: i64,
        #[arg(long)]
        json: bool,
    },
    AddMember {
        #[arg(long, default_value = "127.0.0.1:7420")]
        endpoint: String,
        #[arg(long)]
        manifest_id: String,
        #[arg(long)]
        object_id: String,
        #[arg(long, default_value_t = true)]
        required: bool,
    },
    Inspect {
        #[arg(long, default_value = "127.0.0.1:7420")]
        endpoint: String,
        #[arg(long)]
        manifest_id: String,
        #[arg(long)]
        json: bool,
    },
    List {
        #[arg(long, default_value = "127.0.0.1:7420")]
        endpoint: String,
        #[arg(long)]
        prefix_hash: Option<String>,
        #[arg(long)]
        json: bool,
    },
    Check {
        #[arg(long, default_value = "127.0.0.1:7420")]
        endpoint: String,
        #[arg(long)]
        manifest_id: String,
        #[arg(long)]
        json: bool,
    },
    Pin {
        #[arg(long, default_value = "127.0.0.1:7420")]
        endpoint: String,
        #[arg(long)]
        manifest_id: String,
    },
    Unpin {
        #[arg(long, default_value = "127.0.0.1:7420")]
        endpoint: String,
        #[arg(long)]
        manifest_id: String,
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
        Command::Manifest { command } => run_manifest(command).await,
    };

    match result {
        Ok(code) => std::process::exit(code),
        Err(error) => {
            eprintln!("bifrost-store: error: {error:#}");
            std::process::exit(2);
        }
    }
}

async fn run_manifest(command: ManifestCommand) -> anyhow::Result<i32> {
    match command {
        ManifestCommand::CreatePrefix {
            endpoint,
            prefix_hash,
            model_hash,
            tokenizer_hash,
            rope_config_hash,
            token_range_start,
            token_range_end,
            json,
        } => {
            let outcome = create_prefix_manifest(
                &endpoint,
                Some(model_hash),
                tokenizer_hash,
                rope_config_hash,
                prefix_hash,
                token_range_start,
                token_range_end,
            )
            .await?;
            ensure_manifest_ok(&outcome)?;
            if json {
                println!("{}", serde_json::to_string(&outcome.response)?);
            } else if let Some(manifest) = outcome.response.manifest {
                print_manifest(&manifest.manifest);
            }
            Ok(0)
        }
        ManifestCommand::AddMember {
            endpoint,
            manifest_id,
            object_id,
            required,
        } => {
            let outcome =
                manifest_add_member(&endpoint, &manifest_id, &object_id, required).await?;
            if outcome.reason.is_empty() {
                println!("ok manifest_id={} object_id={}", manifest_id, object_id);
                Ok(0)
            } else {
                println!(
                    "error manifest_id={} object_id={} reason={}",
                    manifest_id, object_id, outcome.reason
                );
                Ok(1)
            }
        }
        ManifestCommand::Inspect {
            endpoint,
            manifest_id,
            json,
        } => {
            let outcome = inspect_manifest(&endpoint, &manifest_id).await?;
            ensure_manifest_ok(&outcome)?;
            if json {
                println!("{}", serde_json::to_string(&outcome.response)?);
            } else if let Some(manifest) = outcome.response.manifest {
                print_manifest(&manifest.manifest);
                for member in &manifest.members {
                    println!(
                        "member object_id={} layer_id={} kv_block_id={} required={}",
                        member.object_id,
                        member
                            .layer_id
                            .map(|value| value.to_string())
                            .unwrap_or_else(|| "-".to_string()),
                        member
                            .kv_block_id
                            .map(|value| value.to_string())
                            .unwrap_or_else(|| "-".to_string()),
                        member.required
                    );
                }
            }
            Ok(0)
        }
        ManifestCommand::List {
            endpoint,
            prefix_hash,
            json,
        } => {
            let outcome = list_manifests(
                &endpoint,
                ManifestListFilter {
                    prefix_hash,
                    ..ManifestListFilter::default()
                },
            )
            .await?;
            ensure_manifest_ok(&outcome)?;
            if json {
                println!("{}", serde_json::to_string(&outcome.response)?);
            } else {
                for manifest in &outcome.response.manifests {
                    print_manifest(manifest);
                }
            }
            Ok(if outcome.response.manifests.is_empty() {
                1
            } else {
                0
            })
        }
        ManifestCommand::Check {
            endpoint,
            manifest_id,
            json,
        } => {
            let outcome = check_manifest(&endpoint, &manifest_id).await?;
            ensure_manifest_ok(&outcome)?;
            if json {
                println!("{}", serde_json::to_string(&outcome.response)?);
            } else if let Some(completeness) = outcome.response.completeness {
                println!(
                    "manifest_id={} completeness_state={} missing={}",
                    completeness.manifest_id,
                    completeness.completeness_state,
                    completeness.missing.len()
                );
            }
            Ok(0)
        }
        ManifestCommand::Pin {
            endpoint,
            manifest_id,
        } => {
            let outcome = manifest_pin(&endpoint, &manifest_id).await?;
            if outcome.reason.is_empty() {
                println!("ok manifest_id={}", manifest_id);
                Ok(0)
            } else {
                println!(
                    "error manifest_id={} reason={}",
                    manifest_id, outcome.reason
                );
                Ok(1)
            }
        }
        ManifestCommand::Unpin {
            endpoint,
            manifest_id,
        } => {
            let outcome = manifest_unpin(&endpoint, &manifest_id).await?;
            if outcome.reason.is_empty() {
                println!("ok manifest_id={}", manifest_id);
                Ok(0)
            } else {
                println!(
                    "error manifest_id={} reason={}",
                    manifest_id, outcome.reason
                );
                Ok(1)
            }
        }
    }
}

fn ensure_manifest_ok(outcome: &bifrostd::transport::StoreManifestOutcome) -> anyhow::Result<()> {
    if outcome.reason.is_empty() {
        Ok(())
    } else {
        anyhow::bail!(outcome.reason.clone())
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

fn print_manifest(manifest: &bifrostd::store::ManifestRecord) {
    println!(
        "manifest_id={} manifest_type={} prefix_hash={} model_hash={} token_range_start={} token_range_end={} completeness_state={} pin_count={}",
        manifest.manifest_id,
        manifest.manifest_type,
        manifest.prefix_hash,
        manifest.model_hash.as_deref().unwrap_or("-"),
        manifest.token_range_start,
        manifest.token_range_end,
        manifest.completeness_state,
        manifest.pin_count
    );
}
