use std::fmt;
use std::str::FromStr;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PathSpec {
    pub name: String,
    pub endpoint: String,
}

impl PathSpec {
    pub fn primary(endpoint: impl Into<String>) -> Self {
        Self {
            name: "primary".to_string(),
            endpoint: endpoint.into(),
        }
    }
}

impl FromStr for PathSpec {
    type Err = String;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        let (name, endpoint) = value
            .split_once('=')
            .ok_or_else(|| "path must be NAME=HOST:PORT".to_string())?;
        if name.is_empty() {
            return Err("path name must not be empty".to_string());
        }
        if endpoint.is_empty() {
            return Err("path endpoint must not be empty".to_string());
        }
        if !name
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-' | b'.'))
        {
            return Err(
                "path name may only contain ASCII letters, digits, '_', '-', or '.'".to_string(),
            );
        }
        Ok(Self {
            name: name.to_string(),
            endpoint: endpoint.to_string(),
        })
    }
}

impl fmt::Display for PathSpec {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{}={}", self.name, self.endpoint)
    }
}
