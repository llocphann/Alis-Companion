use std::collections::BTreeSet;
use std::ffi::{CString, c_char, c_int, c_uint, c_void};
use std::fs;
use std::io::{self, BufRead};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::ptr;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{Map, Value, json};

pub const MAX_ITEMS: usize = 32;
pub const MAX_SELECTION: usize = 8;
pub const MAX_REQUEST: usize = 128 * 1024;
pub const MAX_PATH: usize = 4096;
pub const MAX_INTEGER: i64 = 9_007_199_254_740_991;
const COLUMNS: [&str; 7] = [
    "id",
    "path",
    "name",
    "size",
    "modified_ms",
    "created_ms",
    "source",
];

// Linux x86_64 constants for openat/fstatat
const O_RDONLY: c_int = 0;
const O_RDWR: c_int = 2;
const O_CREAT: c_int = 0o100;
const O_EXCL: c_int = 0o200;
const O_DIRECTORY: c_int = 0o200000;
const O_NOFOLLOW: c_int = 0o400000;
const O_CLOEXEC: c_int = 0o2000000;
const AT_SYMLINK_NOFOLLOW: c_int = 0x100;
const S_IFMT: u32 = 0o170000;
const S_IFREG: u32 = 0o100000;
const S_IFDIR: u32 = 0o040000;

#[repr(C)]
struct libc_stat {
    st_dev: u64,
    st_ino: u64,
    st_nlink: u64,
    st_mode: u32,
    st_uid: u32,
    st_gid: u32,
    __pad0: c_int,
    st_rdev: u64,
    st_size: i64,
    st_blksize: i64,
    st_blocks: i64,
    st_atime: i64,
    st_atime_nsec: i64,
    st_mtime: i64,
    st_mtime_nsec: i64,
    st_ctime: i64,
    st_ctime_nsec: i64,
    __unused: [i64; 3],
}

unsafe extern "C" {
    fn umask(mask: u32) -> u32;
    fn getuid() -> u32;
    fn open(pathname: *const c_char, flags: c_int, ...) -> c_int;
    fn openat(dirfd: c_int, pathname: *const c_char, flags: c_int, ...) -> c_int;
    fn fstatat(
        dirfd: c_int,
        pathname: *const c_char,
        statbuf: *mut libc_stat,
        flags: c_int,
    ) -> c_int;
    fn chmod(pathname: *const c_char, mode: u32) -> c_int;
    fn close(fd: c_int) -> c_int;
}

const SQLITE_OK: c_int = 0;
const SQLITE_ROW: c_int = 100;
const SQLITE_DONE: c_int = 101;
const SQLITE_INTEGER: c_int = 1;
const SQLITE_TEXT: c_int = 3;
const SQLITE_TRANSIENT: isize = -1;

#[link(name = "sqlite3")]
unsafe extern "C" {
    fn sqlite3_open(filename: *const c_char, pp_db: *mut *mut c_void) -> c_int;
    fn sqlite3_close_v2(db: *mut c_void) -> c_int;
    fn sqlite3_busy_timeout(db: *mut c_void, ms: c_int) -> c_int;
    fn sqlite3_exec(
        db: *mut c_void,
        sql: *const c_char,
        callback: Option<
            unsafe extern "C" fn(*mut c_void, c_int, *mut *mut c_char, *mut *mut c_char) -> c_int,
        >,
        arg: *mut c_void,
        errmsg: *mut *mut c_char,
    ) -> c_int;
    fn sqlite3_prepare_v2(
        db: *mut c_void,
        z_sql: *const c_char,
        n_byte: c_int,
        pp_stmt: *mut *mut c_void,
        pz_tail: *mut *const c_char,
    ) -> c_int;
    fn sqlite3_step(stmt: *mut c_void) -> c_int;
    fn sqlite3_finalize(stmt: *mut c_void) -> c_int;
    fn sqlite3_bind_int64(stmt: *mut c_void, idx: c_int, val: i64) -> c_int;
    fn sqlite3_bind_text(
        stmt: *mut c_void,
        idx: c_int,
        val: *const c_char,
        n: c_int,
        destructor: isize,
    ) -> c_int;
    fn sqlite3_column_type(stmt: *mut c_void, i_col: c_int) -> c_int;
    fn sqlite3_column_int64(stmt: *mut c_void, i_col: c_int) -> i64;
    fn sqlite3_column_text(stmt: *mut c_void, i_col: c_int) -> *const u8;
    fn sqlite3_column_bytes(stmt: *mut c_void, i_col: c_int) -> c_int;
    fn sqlite3_changes(db: *mut c_void) -> c_int;
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PocketError(pub &'static str);

impl PocketError {
    pub fn code(&self) -> &'static str {
        self.0
    }
}

fn is_valid_int(val: &Value, minimum: i64) -> Option<i64> {
    let n = val.as_i64()?;
    if (minimum..=MAX_INTEGER).contains(&n) {
        Some(n)
    } else {
        None
    }
}

fn is_unicode_format_char(c: char) -> bool {
    matches!(
        c as u32,
        0x00AD
            | 0x0600..=0x0605
            | 0x061C
            | 0x06DD
            | 0x070F
            | 0x0890..=0x0891
            | 0x08E2
            | 0x180E
            | 0x200B..=0x200F
            | 0x202A..=0x202E
            | 0x2060..=0x2064
            | 0x2066..=0x206F
            | 0xFEFF
            | 0xFFF9..=0xFFFB
            | 0x110BD
            | 0x110CD
            | 0x13430..=0x1343F
            | 0x1BCA0..=0x1BCA3
            | 0x1D173..=0x1D17A
            | 0xE0001
            | 0xE0020..=0xE007F
    )
}

fn is_clean_str(value: &str) -> bool {
    !value
        .chars()
        .any(|c| c.is_control() || is_unicode_format_char(c))
}

pub fn local_path(url: &str) -> Result<String, PocketError> {
    let char_count = url.chars().count();
    if !is_clean_str(url) || !(1..=MAX_PATH * 3).contains(&char_count) {
        return Err(PocketError("invalid_file_url"));
    }
    let Some((scheme, rest)) = url.split_once(':') else {
        return Err(PocketError("local_files_only"));
    };
    if !scheme.eq_ignore_ascii_case("file") || rest.contains('?') || rest.contains('#') {
        return Err(PocketError("local_files_only"));
    }
    let raw_path = if let Some(after_slashes) = rest.strip_prefix("//") {
        let Some(slash_idx) = after_slashes.find('/') else {
            if !after_slashes.is_empty() {
                return Err(PocketError("local_files_only"));
            }
            return Err(PocketError("invalid_file_url"));
        };
        let netloc = &after_slashes[..slash_idx];
        if !netloc.is_empty() {
            return Err(PocketError("local_files_only"));
        }
        &after_slashes[slash_idx..]
    } else {
        rest
    };

    let raw_bytes = raw_path.as_bytes();
    let mut decoded = Vec::with_capacity(raw_bytes.len());
    let mut i = 0;
    while i < raw_bytes.len() {
        if raw_bytes[i] == b'%' {
            if i + 2 >= raw_bytes.len() {
                return Err(PocketError("invalid_file_url"));
            }
            let h1 = (raw_bytes[i + 1] as char).to_digit(16);
            let h2 = (raw_bytes[i + 2] as char).to_digit(16);
            match (h1, h2) {
                (Some(d1), Some(d2)) => {
                    decoded.push(((d1 << 4) | d2) as u8);
                    i += 3;
                }
                _ => return Err(PocketError("invalid_file_url")),
            }
        } else {
            decoded.push(raw_bytes[i]);
            i += 1;
        }
    }

    let value = String::from_utf8(decoded).map_err(|_| PocketError("invalid_file_url"))?;
    let vlen = value.chars().count();
    if !is_clean_str(&value) || vlen <= 1 || vlen > MAX_PATH || !value.starts_with('/') {
        return Err(PocketError("invalid_file_url"));
    }
    let parts: Vec<&str> = value.split('/').collect();
    if parts.len() > 128
        || parts[1..]
            .iter()
            .any(|&p| p.is_empty() || p == "." || p == "..")
    {
        return Err(PocketError("invalid_file_url"));
    }
    Ok(value)
}

#[derive(Debug, Clone)]
pub struct FileMeta {
    pub path: String,
    pub name: String,
    pub size: i64,
    pub modified_ms: i64,
}

pub fn metadata(url: &str) -> Result<FileMeta, PocketError> {
    let value = local_path(url)?;
    let parts: Vec<&str> = value.split('/').skip(1).collect();
    let root_c = CString::new("/").unwrap();
    let mut directory = unsafe { open(root_c.as_ptr(), O_RDONLY | O_DIRECTORY | O_CLOEXEC) };
    if directory < 0 {
        return Err(PocketError("file_unavailable"));
    }

    let mut stat_buf: libc_stat = unsafe { std::mem::zeroed() };
    let traverse_res = (|| -> Result<(), PocketError> {
        for &part in &parts[..parts.len() - 1] {
            let c_part = CString::new(part).map_err(|_| PocketError("invalid_file_url"))?;
            let child = unsafe {
                openat(
                    directory,
                    c_part.as_ptr(),
                    O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC,
                )
            };
            if child < 0 {
                return Err(PocketError("file_unavailable"));
            }
            unsafe {
                close(directory);
            }
            directory = child;
        }
        let c_last =
            CString::new(*parts.last().unwrap()).map_err(|_| PocketError("invalid_file_url"))?;
        let rc = unsafe {
            fstatat(
                directory,
                c_last.as_ptr(),
                &mut stat_buf,
                AT_SYMLINK_NOFOLLOW,
            )
        };
        if rc != 0 {
            return Err(PocketError("file_unavailable"));
        }
        Ok(())
    })();

    unsafe {
        close(directory);
    }
    traverse_res?;

    if (stat_buf.st_mode & S_IFMT) != S_IFREG {
        return Err(PocketError("regular_files_only"));
    }
    let modified = stat_buf
        .st_mtime
        .saturating_mul(1000)
        .saturating_add(stat_buf.st_mtime_nsec.div_euclid(1_000_000));
    if !(0..=MAX_INTEGER).contains(&stat_buf.st_size) || !(0..=MAX_INTEGER).contains(&modified) {
        return Err(PocketError("unsupported_metadata"));
    }

    let name = (*parts.last().unwrap()).to_owned();
    Ok(FileMeta {
        path: value,
        name,
        size: stat_buf.st_size,
        modified_ms: modified,
    })
}

pub fn store_path() -> PathBuf {
    let root = std::env::var_os("XDG_STATE_HOME")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            let home = std::env::var_os("HOME")
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("/tmp"));
            home.join(".local/state")
        });
    root.join("alis/companion/pocket.sqlite3")
}

struct SqliteDb {
    ptr: *mut c_void,
}

impl Drop for SqliteDb {
    fn drop(&mut self) {
        if !self.ptr.is_null() {
            unsafe {
                sqlite3_close_v2(self.ptr);
            }
        }
    }
}

impl SqliteDb {
    fn open(path: &Path) -> Result<Self, PocketError> {
        let c_path = CString::new(path.as_os_str().as_bytes())
            .map_err(|_| PocketError("store_unavailable"))?;
        let mut ptr: *mut c_void = ptr::null_mut();
        let rc = unsafe { sqlite3_open(c_path.as_ptr(), &mut ptr) };
        if rc != SQLITE_OK || ptr.is_null() {
            if !ptr.is_null() {
                unsafe {
                    sqlite3_close_v2(ptr);
                }
            }
            return Err(PocketError("store_unavailable"));
        }
        unsafe {
            sqlite3_busy_timeout(ptr, 1500);
        }
        Ok(Self { ptr })
    }

    fn exec(&self, sql: &str) -> Result<(), PocketError> {
        let c_sql = CString::new(sql).map_err(|_| PocketError("store_unavailable"))?;
        let rc =
            unsafe { sqlite3_exec(self.ptr, c_sql.as_ptr(), None, ptr::null_mut(), ptr::null_mut()) };
        if rc != SQLITE_OK {
            return Err(PocketError("store_unavailable"));
        }
        Ok(())
    }

    fn query_user_version(&self) -> Result<i64, PocketError> {
        let c_sql = CString::new("PRAGMA user_version").unwrap();
        let mut stmt: *mut c_void = ptr::null_mut();
        let rc =
            unsafe { sqlite3_prepare_v2(self.ptr, c_sql.as_ptr(), -1, &mut stmt, ptr::null_mut()) };
        if rc != SQLITE_OK || stmt.is_null() {
            return Err(PocketError("store_unavailable"));
        }
        let step_rc = unsafe { sqlite3_step(stmt) };
        let version = if step_rc == SQLITE_ROW {
            unsafe { sqlite3_column_int64(stmt, 0) }
        } else {
            unsafe {
                sqlite3_finalize(stmt);
            }
            return Err(PocketError("store_unavailable"));
        };
        unsafe {
            sqlite3_finalize(stmt);
        }
        Ok(version)
    }

    fn query_tables(&self) -> Result<BTreeSet<String>, PocketError> {
        let c_sql = CString::new("SELECT name FROM sqlite_master WHERE type='table'").unwrap();
        let mut stmt: *mut c_void = ptr::null_mut();
        let rc =
            unsafe { sqlite3_prepare_v2(self.ptr, c_sql.as_ptr(), -1, &mut stmt, ptr::null_mut()) };
        if rc != SQLITE_OK || stmt.is_null() {
            return Err(PocketError("store_unavailable"));
        }
        let mut tables = BTreeSet::new();
        loop {
            let s = unsafe { sqlite3_step(stmt) };
            if s == SQLITE_ROW {
                let text_ptr = unsafe { sqlite3_column_text(stmt, 0) };
                let len = unsafe { sqlite3_column_bytes(stmt, 0) } as usize;
                if !text_ptr.is_null() {
                    let slice = unsafe { std::slice::from_raw_parts(text_ptr, len) };
                    tables.insert(String::from_utf8_lossy(slice).into_owned());
                }
            } else if s == SQLITE_DONE {
                break;
            } else {
                unsafe {
                    sqlite3_finalize(stmt);
                }
                return Err(PocketError("store_unavailable"));
            }
        }
        unsafe {
            sqlite3_finalize(stmt);
        }
        Ok(tables)
    }

    fn query_item_columns(&self) -> Result<Vec<String>, PocketError> {
        let c_sql = CString::new("PRAGMA table_info(items)").unwrap();
        let mut stmt: *mut c_void = ptr::null_mut();
        let rc =
            unsafe { sqlite3_prepare_v2(self.ptr, c_sql.as_ptr(), -1, &mut stmt, ptr::null_mut()) };
        if rc != SQLITE_OK || stmt.is_null() {
            return Err(PocketError("store_unavailable"));
        }
        let mut cols = Vec::new();
        loop {
            let s = unsafe { sqlite3_step(stmt) };
            if s == SQLITE_ROW {
                let text_ptr = unsafe { sqlite3_column_text(stmt, 1) };
                let len = unsafe { sqlite3_column_bytes(stmt, 1) } as usize;
                if !text_ptr.is_null() {
                    let slice = unsafe { std::slice::from_raw_parts(text_ptr, len) };
                    cols.push(String::from_utf8_lossy(slice).into_owned());
                }
            } else if s == SQLITE_DONE {
                break;
            } else {
                unsafe {
                    sqlite3_finalize(stmt);
                }
                return Err(PocketError("store_unavailable"));
            }
        }
        unsafe {
            sqlite3_finalize(stmt);
        }
        Ok(cols)
    }

    fn changes(&self) -> i64 {
        unsafe { sqlite3_changes(self.ptr) as i64 }
    }
}

fn connect(create: bool) -> Result<Option<SqliteDb>, PocketError> {
    let target = store_path();
    let target_lstat = fs::symlink_metadata(&target);
    if target_lstat.is_err() && !create {
        return Ok(None);
    }

    let uid = unsafe { getuid() };
    let Some(parent_path) = target.parent() else {
        return Err(PocketError("unsafe_store"));
    };
    fs::create_dir_all(parent_path).map_err(|_| PocketError("store_unavailable"))?;
    let parent_meta =
        fs::symlink_metadata(parent_path).map_err(|_| PocketError("store_unavailable"))?;
    if (parent_meta.mode() & S_IFMT) != S_IFDIR || parent_meta.uid() != uid {
        return Err(PocketError("unsafe_store"));
    }

    if fs::symlink_metadata(&target).is_err() {
        let c_target = CString::new(target.as_os_str().as_bytes())
            .map_err(|_| PocketError("store_unavailable"))?;
        let fd = unsafe {
            open(
                c_target.as_ptr(),
                O_RDWR | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC,
                0o600 as c_uint,
            )
        };
        if fd < 0 {
            return Err(PocketError("store_unavailable"));
        }
        unsafe {
            close(fd);
        }
    }

    let info = fs::symlink_metadata(&target).map_err(|_| PocketError("store_unavailable"))?;
    if (info.mode() & S_IFMT) != S_IFREG || info.uid() != uid || info.nlink() != 1 {
        return Err(PocketError("unsafe_store"));
    }

    for suffix in ["-journal", "-wal", "-shm"] {
        let sidecar = PathBuf::from(format!("{}{}", target.to_string_lossy(), suffix));
        if let Ok(side) = fs::symlink_metadata(&sidecar) {
            if (side.mode() & S_IFMT) != S_IFREG || side.uid() != uid || side.nlink() != 1 {
                return Err(PocketError("unsafe_store"));
            }
        }
    }

    let db = SqliteDb::open(&target)?;
    let version = db.query_user_version()?;
    let tables = db.query_tables()?;
    let has_foreign = tables
        .iter()
        .any(|t| t != "items" && t != "sqlite_sequence");
    if (version != 0 && version != 1) || has_foreign {
        return Err(PocketError("unsupported_store_version"));
    }
    if tables.contains("items") {
        let cols = db.query_item_columns()?;
        if cols != COLUMNS || version != 1 {
            return Err(PocketError("unsupported_store_version"));
        }
    } else if !tables.is_empty() || version != 0 {
        return Err(PocketError("unsupported_store_version"));
    }

    let c_target = CString::new(target.as_os_str().as_bytes())
        .map_err(|_| PocketError("store_unavailable"))?;
    if unsafe { chmod(c_target.as_ptr(), 0o600) } != 0 {
        return Err(PocketError("store_unavailable"));
    }

    db.exec("PRAGMA busy_timeout=1500")?;
    db.exec("PRAGMA synchronous=FULL")?;
    if tables.is_empty() {
        db.exec("BEGIN IMMEDIATE")?;
        if let Err(e) = db
            .exec(
                "CREATE TABLE items(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
                    size INTEGER NOT NULL, modified_ms INTEGER NOT NULL,
                    created_ms INTEGER NOT NULL,
                    source TEXT NOT NULL CHECK(source IN ('picker','drop'))
                )",
            )
            .and_then(|()| db.exec("PRAGMA user_version=1"))
            .and_then(|()| db.exec("COMMIT"))
        {
            let _ = db.exec("ROLLBACK");
            return Err(e);
        }
    }
    Ok(Some(db))
}

fn rows(db: Option<&SqliteDb>) -> Result<Vec<Value>, PocketError> {
    let Some(db) = db else {
        return Ok(Vec::new());
    };
    let sql = format!(
        "SELECT {} FROM items ORDER BY id DESC LIMIT {}",
        COLUMNS.join(","),
        MAX_ITEMS + 1
    );
    let c_sql = CString::new(sql).unwrap();
    let mut stmt: *mut c_void = ptr::null_mut();
    let rc =
        unsafe { sqlite3_prepare_v2(db.ptr, c_sql.as_ptr(), -1, &mut stmt, ptr::null_mut()) };
    if rc != SQLITE_OK || stmt.is_null() {
        return Err(PocketError("store_unavailable"));
    }

    let mut result = Vec::new();
    loop {
        let s = unsafe { sqlite3_step(stmt) };
        if s == SQLITE_ROW {
            if result.len() >= MAX_ITEMS {
                unsafe {
                    sqlite3_finalize(stmt);
                }
                return Err(PocketError("invalid_stored_metadata"));
            }
            let id_type = unsafe { sqlite3_column_type(stmt, 0) };
            let path_type = unsafe { sqlite3_column_type(stmt, 1) };
            let name_type = unsafe { sqlite3_column_type(stmt, 2) };
            let size_type = unsafe { sqlite3_column_type(stmt, 3) };
            let mod_type = unsafe { sqlite3_column_type(stmt, 4) };
            let cre_type = unsafe { sqlite3_column_type(stmt, 5) };
            let src_type = unsafe { sqlite3_column_type(stmt, 6) };
            if id_type != SQLITE_INTEGER
                || path_type != SQLITE_TEXT
                || name_type != SQLITE_TEXT
                || size_type != SQLITE_INTEGER
                || mod_type != SQLITE_INTEGER
                || cre_type != SQLITE_INTEGER
                || src_type != SQLITE_TEXT
            {
                unsafe {
                    sqlite3_finalize(stmt);
                }
                return Err(PocketError("invalid_stored_metadata"));
            }

            let id = unsafe { sqlite3_column_int64(stmt, 0) };
            let size = unsafe { sqlite3_column_int64(stmt, 3) };
            let modified_ms = unsafe { sqlite3_column_int64(stmt, 4) };
            let created_ms = unsafe { sqlite3_column_int64(stmt, 5) };

            let read_col_str = |col: c_int| -> Result<String, PocketError> {
                let ptr = unsafe { sqlite3_column_text(stmt, col) };
                let len = unsafe { sqlite3_column_bytes(stmt, col) } as usize;
                if ptr.is_null() {
                    return Err(PocketError("invalid_stored_metadata"));
                }
                let slice = unsafe { std::slice::from_raw_parts(ptr, len) };
                std::str::from_utf8(slice)
                    .map(str::to_owned)
                    .map_err(|_| PocketError("invalid_stored_metadata"))
            };

            let Ok(path_val) = read_col_str(1) else {
                unsafe {
                    sqlite3_finalize(stmt);
                }
                return Err(PocketError("invalid_stored_metadata"));
            };
            let Ok(name_val) = read_col_str(2) else {
                unsafe {
                    sqlite3_finalize(stmt);
                }
                return Err(PocketError("invalid_stored_metadata"));
            };
            let Ok(source_val) = read_col_str(6) else {
                unsafe {
                    sqlite3_finalize(stmt);
                }
                return Err(PocketError("invalid_stored_metadata"));
            };

            let path_chars = path_val.chars().count();
            let name_chars = name_val.chars().count();
            if !(1..=MAX_INTEGER).contains(&id)
                || !is_clean_str(&path_val)
                || path_chars <= 1
                || path_chars > MAX_PATH
                || !path_val.starts_with('/')
                || !is_clean_str(&name_val)
                || !(1..=255).contains(&name_chars)
                || (source_val != "picker" && source_val != "drop")
                || !(0..=MAX_INTEGER).contains(&size)
                || !(0..=MAX_INTEGER).contains(&modified_ms)
                || !(0..=MAX_INTEGER).contains(&created_ms)
            {
                unsafe {
                    sqlite3_finalize(stmt);
                }
                return Err(PocketError("invalid_stored_metadata"));
            }

            result.push(json!({
                "id": id,
                "path": path_val,
                "name": name_val,
                "size": size,
                "modified_ms": modified_ms,
                "created_ms": created_ms,
                "source": source_val,
            }));
        } else if s == SQLITE_DONE {
            break;
        } else {
            unsafe {
                sqlite3_finalize(stmt);
            }
            return Err(PocketError("store_unavailable"));
        }
    }
    unsafe {
        sqlite3_finalize(stmt);
    }
    Ok(result)
}

pub fn dispatch(request: &Value) -> Result<Value, PocketError> {
    let Some(obj) = request.as_object() else {
        return Err(PocketError("explicit_consent_required"));
    };
    let Some(ver) = obj.get("version").and_then(|v| is_valid_int(v, 1)) else {
        return Err(PocketError("explicit_consent_required"));
    };
    if ver != 1 || obj.get("consent") != Some(&Value::Bool(true)) {
        return Err(PocketError("explicit_consent_required"));
    }

    let Some(action) = obj.get("action").and_then(Value::as_str) else {
        return Err(PocketError("invalid_request"));
    };
    let expected_keys: BTreeSet<&str> = match action {
        "list" => ["version", "action", "consent"].into_iter().collect(),
        "add" => ["version", "action", "consent", "urls", "source"]
            .into_iter()
            .collect(),
        "remove" => ["version", "action", "consent", "id"].into_iter().collect(),
        "clear" => ["version", "action", "consent", "confirmed"]
            .into_iter()
            .collect(),
        _ => return Err(PocketError("invalid_request")),
    };
    let actual_keys: BTreeSet<&str> = obj.keys().map(String::as_str).collect();
    if actual_keys != expected_keys {
        return Err(PocketError("invalid_request"));
    }

    let mut selection: Vec<FileMeta> = Vec::new();
    let mut source_str = "";
    let mut remove_id = 0_i64;

    if action == "add" {
        source_str = obj
            .get("source")
            .and_then(Value::as_str)
            .ok_or(PocketError("selection_limit"))?;
        let urls = obj
            .get("urls")
            .and_then(Value::as_array)
            .ok_or(PocketError("selection_limit"))?;
        if (source_str != "picker" && source_str != "drop")
            || urls.is_empty()
            || urls.len() > MAX_SELECTION
        {
            return Err(PocketError("selection_limit"));
        }
        for u in urls {
            let url_s = u.as_str().ok_or(PocketError("invalid_file_url"))?;
            let meta = metadata(url_s)?;
            if let Some(existing) = selection.iter_mut().find(|m| m.path == meta.path) {
                *existing = meta;
            } else {
                selection.push(meta);
            }
        }
    } else if action == "remove" {
        remove_id = obj
            .get("id")
            .and_then(|v| is_valid_int(v, 1))
            .ok_or(PocketError("invalid_request"))?;
    } else if action == "clear" {
        if obj.get("confirmed") != Some(&Value::Bool(true)) {
            return Err(PocketError("explicit_consent_required"));
        }
    }

    let db = connect(action == "add")?;
    let mut existing = rows(db.as_ref())?;
    let mut changed = 0_i64;

    if let Some(ref db) = db
        && action != "list"
    {
        db.exec("BEGIN IMMEDIATE")?;
        let tx_res = (|| -> Result<(), PocketError> {
            if action == "add" {
                let current_rows = rows(Some(db))?;
                let mut known: BTreeSet<String> = current_rows
                    .iter()
                    .filter_map(|r| r.get("path").and_then(Value::as_str).map(str::to_owned))
                    .collect();
                for item in &selection {
                    known.insert(item.path.clone());
                }
                if known.len() > MAX_ITEMS {
                    return Err(PocketError("pocket_full"));
                }
                let now = SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .map(|d| d.as_millis() as i64)
                    .unwrap_or(0);
                let c_sql = CString::new(
                    "INSERT INTO items(path,name,size,modified_ms,created_ms,source) \
                     VALUES(?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET \
                     name=excluded.name,size=excluded.size,modified_ms=excluded.modified_ms",
                )
                .unwrap();
                for item in &selection {
                    let mut stmt: *mut c_void = ptr::null_mut();
                    let rc = unsafe {
                        sqlite3_prepare_v2(
                            db.ptr,
                            c_sql.as_ptr(),
                            -1,
                            &mut stmt,
                            ptr::null_mut(),
                        )
                    };
                    if rc != SQLITE_OK || stmt.is_null() {
                        return Err(PocketError("store_unavailable"));
                    }
                    let c_path = CString::new(item.path.as_str()).unwrap();
                    let c_name = CString::new(item.name.as_str()).unwrap();
                    let c_src = CString::new(source_str).unwrap();
                    unsafe {
                        sqlite3_bind_text(
                            stmt,
                            1,
                            c_path.as_ptr(),
                            item.path.len() as c_int,
                            SQLITE_TRANSIENT,
                        );
                        sqlite3_bind_text(
                            stmt,
                            2,
                            c_name.as_ptr(),
                            item.name.len() as c_int,
                            SQLITE_TRANSIENT,
                        );
                        sqlite3_bind_int64(stmt, 3, item.size);
                        sqlite3_bind_int64(stmt, 4, item.modified_ms);
                        sqlite3_bind_int64(stmt, 5, now);
                        sqlite3_bind_text(
                            stmt,
                            6,
                            c_src.as_ptr(),
                            source_str.len() as c_int,
                            SQLITE_TRANSIENT,
                        );
                    }
                    let step_rc = unsafe { sqlite3_step(stmt) };
                    unsafe {
                        sqlite3_finalize(stmt);
                    }
                    if step_rc != SQLITE_DONE {
                        return Err(PocketError("store_unavailable"));
                    }
                    changed += 1;
                }
            } else if action == "remove" {
                let c_sql = CString::new("DELETE FROM items WHERE id=?").unwrap();
                let mut stmt: *mut c_void = ptr::null_mut();
                let rc = unsafe {
                    sqlite3_prepare_v2(db.ptr, c_sql.as_ptr(), -1, &mut stmt, ptr::null_mut())
                };
                if rc != SQLITE_OK || stmt.is_null() {
                    return Err(PocketError("store_unavailable"));
                }
                unsafe {
                    sqlite3_bind_int64(stmt, 1, remove_id);
                }
                let step_rc = unsafe { sqlite3_step(stmt) };
                unsafe {
                    sqlite3_finalize(stmt);
                }
                if step_rc != SQLITE_DONE {
                    return Err(PocketError("store_unavailable"));
                }
                changed = db.changes();
            } else if action == "clear" {
                db.exec("DELETE FROM items")?;
                changed = db.changes();
            }
            existing = rows(Some(db))?;
            Ok(())
        })();

        match tx_res {
            Ok(()) => {
                db.exec("COMMIT")?;
            }
            Err(err) => {
                let _ = db.exec("ROLLBACK");
                return Err(err);
            }
        }
    }

    let mut out = Map::new();
    out.insert("version".into(), json!(1));
    out.insert("ok".into(), json!(true));
    out.insert("action".into(), json!(action));
    out.insert("items".into(), Value::Array(existing));
    out.insert("changed".into(), json!(changed));
    Ok(Value::Object(out))
}

pub fn run_cli() -> i32 {
    unsafe {
        umask(0o077);
    }
    let stdin = io::stdin();
    let mut reader = stdin.lock();
    let mut line = Vec::new();

    let result = (|| -> Result<Value, PocketError> {
        loop {
            let available = reader
                .fill_buf()
                .map_err(|_| PocketError("invalid_request"))?;
            if available.is_empty() {
                break;
            }
            if let Some(pos) = available.iter().position(|&b| b == b'\n') {
                let take = pos + 1;
                if line.len() + take > MAX_REQUEST {
                    reader.consume(take);
                    return Err(PocketError("selection_limit"));
                }
                line.extend_from_slice(&available[..take]);
                reader.consume(take);
                break;
            }
            let len = available.len();
            if line.len() + len > MAX_REQUEST {
                reader.consume(len);
                return Err(PocketError("selection_limit"));
            }
            line.extend_from_slice(available);
            reader.consume(len);
        }
        let parsed: Value =
            serde_json::from_slice(&line).map_err(|_| PocketError("invalid_request"))?;
        dispatch(&parsed)
    })();

    match result {
        Ok(val) => {
            println!("{}", serde_json::to_string(&val).unwrap_or_default());
            0
        }
        Err(err) => {
            let mut out = Map::new();
            out.insert("version".into(), json!(1));
            out.insert("ok".into(), json!(false));
            out.insert("error".into(), json!(err.code()));
            println!(
                "{}",
                serde_json::to_string(&Value::Object(out)).unwrap_or_default()
            );
            1
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validates_local_file_urls_and_rejects_traversal_or_remote() {
        assert_eq!(local_path("file:///tmp/hello%20world.txt").unwrap(), "/tmp/hello world.txt");
        assert_eq!(
            local_path("https://example.com/a").unwrap_err(),
            PocketError("local_files_only")
        );
        assert_eq!(
            local_path("file://remote/a").unwrap_err(),
            PocketError("local_files_only")
        );
        assert_eq!(
            local_path("file:///a%2Fb/../c").unwrap_err(),
            PocketError("invalid_file_url")
        );
        assert_eq!(
            local_path("file:///bad%GG").unwrap_err(),
            PocketError("invalid_file_url")
        );
    }

    #[test]
    fn enforces_explicit_consent_and_version_contract() {
        assert_eq!(
            dispatch(&json!({"version": 1, "consent": false, "action": "list"})).unwrap_err(),
            PocketError("explicit_consent_required")
        );
        assert_eq!(
            dispatch(&json!({"version": true, "consent": true, "action": "list"})).unwrap_err(),
            PocketError("explicit_consent_required")
        );
        assert_eq!(
            dispatch(&json!({"version": 1, "consent": true, "action": "unknown"})).unwrap_err(),
            PocketError("invalid_request")
        );
    }
}
