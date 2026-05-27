use super::{fsync_dir, write_file_sync, SpoolResult};
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommittedObjectPaths {
    pub metadata: PathBuf,
    pub payload: PathBuf,
}

pub fn atomic_commit(
    paths: &CommittedObjectPaths,
    metadata_bytes: &[u8],
    payload: &[u8],
) -> SpoolResult<()> {
    let dir = paths
        .metadata
        .parent()
        .expect("committed metadata path has a parent");
    fs::create_dir_all(dir)?;

    let tmp_meta = temp_path(&paths.metadata);
    let tmp_payload = temp_path(&paths.payload);
    write_file_sync(&tmp_meta, metadata_bytes)?;
    write_file_sync(&tmp_payload, payload)?;
    fsync_dir(dir)?;

    fs::rename(&tmp_payload, &paths.payload)?;
    fsync_dir(dir)?;
    fs::rename(&tmp_meta, &paths.metadata)?;
    fsync_dir(dir)?;
    Ok(())
}

fn temp_path(final_path: &Path) -> PathBuf {
    let file_name = final_path
        .file_name()
        .expect("committed path has file name")
        .to_string_lossy();
    final_path.with_file_name(format!(".{file_name}.tmp-{}", std::process::id()))
}
