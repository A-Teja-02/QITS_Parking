-- =====================================================================
-- Quadrant Parking - MySQL updates for the CCTV / slot-name release
-- ---------------------------------------------------------------------
-- NO table is created, dropped or altered in a way the old app cares about.
-- The new features use existing columns only:
--   * slot names        -> parking_slots.label          (already exists)
--   * new roles          -> employees.role  VARCHAR(20)  (director, coo, ceo ... fit)
--   * reserved slots     -> parking_slots.reserved_for_manager_id (already exists)
--   * violations         -> stored by the AI server, not in MySQL
--
-- Run in MySQL Workbench, PART by PART. Change `parking_db` if your schema has another name.
-- PART 1 only reads. PART 2-4 are optional and each can be undone (see PART 5).
-- PART 7 adds 3 NEW tables for CCTV (violations, their photos, camera settings) - no existing table is touched.
-- =====================================================================

USE parking_db;

-- =====================================================================
-- PART 1. CHECK (read-only) - run this first and look at the results
-- =====================================================================

-- 1a. The tables the app uses must all exist (expect 9 rows)
SELECT table_name FROM information_schema.tables
WHERE table_schema = DATABASE()
  AND table_name IN ('employees','floors','parking_slots','reservations','manager_releases',
                     'notifications','device_tokens','otp_verifications','password_resets')
ORDER BY table_name;

-- 1b. Column sizes the new features rely on (role >= 20, label >= 50)
SELECT table_name, column_name, column_type
FROM information_schema.columns
WHERE table_schema = DATABASE()
  AND ((table_name = 'employees' AND column_name = 'role')
    OR (table_name = 'parking_slots' AND column_name IN ('label','reserved_for_manager_id')));

-- 1c. Current slots
SELECT s.id, s.label, f.label AS floor, s.position, s.status, s.reserved_for_manager_id
FROM parking_slots s JOIN floors f ON f.id = s.floor_id
ORDER BY f.`order`, s.position;

-- 1d. Slot names used more than once (must be empty before PART 3)
SELECT label, COUNT(*) AS times_used FROM parking_slots
GROUP BY label HAVING COUNT(*) > 1;

-- 1e. Preview of the new names from PART 2 - nothing is changed here
SELECT s.id, s.label AS current_name, CONCAT('106A-', s.position, '-', f.label) AS new_name
FROM parking_slots s JOIN floors f ON f.id = s.floor_id
WHERE s.label = '106A' OR s.label LIKE '106A-%'
ORDER BY f.`order`, s.position;

-- 1f. Two slots with the same floor + position would get the same new name (must be empty)
SELECT floor_id, position, COUNT(*) AS n FROM parking_slots
GROUP BY floor_id, position HAVING COUNT(*) > 1;


-- =====================================================================
-- PART 2. (optional) Rename 106A slots to 106A-<position>-<floor>
--         e.g. 106A-1-B1 ... 106A-8-B1, 106A-1-B2 ... 106A-8-B3
--         Only the visible NAME changes. Slot ids, bookings and
--         reserved-slot assignments are untouched.
--         Admins can also rename any slot later in Admin -> Parking Lots.
-- =====================================================================

-- Backup copy of the slots table (restore steps in PART 5)
CREATE TABLE IF NOT EXISTS parking_slots_backup_cctv AS SELECT * FROM parking_slots;

SET @old_safe_updates = @@SQL_SAFE_UPDATES;
SET SQL_SAFE_UPDATES = 0;
START TRANSACTION;
UPDATE parking_slots s
JOIN floors f ON f.id = s.floor_id
SET s.label = CONCAT('106A-', s.position, '-', f.label)
WHERE s.label = '106A' OR s.label LIKE '106A-%';
COMMIT;
SET SQL_SAFE_UPDATES = @old_safe_updates;

SELECT s.id, s.label, f.label AS floor FROM parking_slots s JOIN floors f ON f.id = s.floor_id
ORDER BY f.`order`, s.position;


-- =====================================================================
-- PART 3. (optional) Database-level guard: no two slots with the same name.
--         The new app already refuses duplicates; this also protects
--         against manual edits. Skips itself if it already exists.
--         Fails with "Duplicate entry" if 1d is not empty - fix names first.
-- =====================================================================
SET @has = (SELECT COUNT(*) FROM information_schema.statistics
            WHERE table_schema = DATABASE() AND table_name = 'parking_slots'
              AND index_name = 'uq_parking_slots_label');
SET @sql = IF(@has = 0,
              'ALTER TABLE parking_slots ADD UNIQUE INDEX uq_parking_slots_label (label)',
              'SELECT ''uq_parking_slots_label already exists'' AS info');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;


-- =====================================================================
-- PART 4. (optional, recommended) Speed indexes for the live CCTV checks.
--         Plain indexes only - no effect on data or on the old app.
--         Each one is skipped if it already exists.
-- =====================================================================
SET @has = (SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema = DATABASE()
            AND table_name = 'reservations' AND index_name = 'ix_reservations_date_status');
SET @sql = IF(@has = 0, 'CREATE INDEX ix_reservations_date_status ON reservations (`date`, status)', 'SELECT 1');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @has = (SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema = DATABASE()
            AND table_name = 'manager_releases' AND index_name = 'ix_manager_releases_date');
SET @sql = IF(@has = 0, 'CREATE INDEX ix_manager_releases_date ON manager_releases (release_date)', 'SELECT 1');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- Roles: nothing to run. To give someone a reserved-slot role, e.g.:
-- UPDATE employees SET role = 'ceo' WHERE email = 'someone@quadrantitservices.com';
-- Allowed values: employee, manager, director, vp, cto, cfo, coo, ceo, admin, hr


-- =====================================================================
-- PART 5. UNDO (only if needed)
-- =====================================================================
-- Remove the unique name guard:
-- ALTER TABLE parking_slots DROP INDEX uq_parking_slots_label;
--
-- Put the old slot names back from the backup:
-- SET SQL_SAFE_UPDATES = 0;
-- UPDATE parking_slots s JOIN parking_slots_backup_cctv b ON b.id = s.id SET s.label = b.label;
-- SET SQL_SAFE_UPDATES = 1;
--
-- Remove the speed indexes:
-- DROP INDEX ix_reservations_date_status ON reservations;
-- DROP INDEX ix_manager_releases_date ON manager_releases;
--
-- When everything is confirmed fine, the backup can be dropped:
-- DROP TABLE parking_slots_backup_cctv;


-- =====================================================================
-- PART 6. (for deployment) App login for the servers - run once as root.
--         Use a strong password; the deploying developer puts it in
--         DATABASE_URL=mysql+pymysql://parking_app:<password>@<host>:3306/parking_db
-- =====================================================================
-- CREATE USER IF NOT EXISTS 'parking_app'@'%' IDENTIFIED BY 'CHANGE_THIS_PASSWORD';
-- GRANT SELECT, INSERT, UPDATE, DELETE ON parking_db.* TO 'parking_app'@'%';
-- FLUSH PRIVILEGES;


-- =====================================================================
-- PART 7. NEW tables for CCTV (violations, evidence photos, camera settings)
--         Only creates tables that don't exist yet; existing tables untouched.
--         The AI server creates these itself if its DB user may CREATE tables;
--         run this when the app user only has SELECT/INSERT/UPDATE/DELETE.
-- =====================================================================
CREATE TABLE IF NOT EXISTS cctv_violations (
    id               VARCHAR(64)  NOT NULL PRIMARY KEY,
    camera_id        VARCHAR(64),
    camera_name      VARCHAR(200),
    slot_id          VARCHAR(64),
    slot_label       VARCHAR(100),
    floor            VARCHAR(100),
    violation_type   VARCHAR(40),     -- WRONG_SLOT | NO_BOOKING | UNKNOWN_VEHICLE | UNREADABLE_PLATE
    severity         VARCHAR(20),
    detected_plate   VARCHAR(40),
    raw_plate        VARCHAR(40),
    plate_confidence DOUBLE,
    plate_match      VARCHAR(20),
    plate_reads      INTEGER,
    matched_user     TEXT,            -- JSON
    expected         TEXT,            -- JSON
    message          TEXT,
    snapshot_url     VARCHAR(300),
    vehicle_crop_url VARCHAR(300),
    plate_crop_url   VARCHAR(300),
    occupied_since   VARCHAR(32),
    vehicle_left_at  VARCHAR(32),
    created_at       VARCHAR(32),     -- ISO time text, sorts by date
    status           VARCHAR(20),     -- open | resolved | false_alarm
    notes            TEXT,
    corrected_plate  VARCHAR(40),
    updated_at       VARCHAR(32),
    INDEX ix_cctv_violations_created (created_at),
    INDEX ix_cctv_violations_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS cctv_violation_images (
    violation_id VARCHAR(64) NOT NULL,
    kind         VARCHAR(20) NOT NULL,   -- scene | vehicle | plate
    content      MEDIUMBLOB  NOT NULL,   -- JPEG (up to 16 MB)
    created_at   VARCHAR(32),
    PRIMARY KEY (violation_id, kind)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS cctv_cameras (
    id         VARCHAR(64) NOT NULL PRIMARY KEY,
    config     TEXT        NOT NULL,     -- camera settings + slot layout (JSON)
    updated_at VARCHAR(32)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Housekeeping example (keep 1 year of violation history):
-- DELETE i FROM cctv_violation_images i JOIN cctv_violations v ON v.id = i.violation_id
--   WHERE v.created_at < DATE_FORMAT(NOW() - INTERVAL 1 YEAR, '%Y-%m-%d');
-- DELETE FROM cctv_violations WHERE created_at < DATE_FORMAT(NOW() - INTERVAL 1 YEAR, '%Y-%m-%d');
