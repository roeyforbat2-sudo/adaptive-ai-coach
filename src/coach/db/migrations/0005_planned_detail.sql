-- 0005: a short session description for planned workouts (e.g. "12 km easy").
-- Filled from the private properties of calendar events the app wrote; untrusted text.
ALTER TABLE planned_workouts ADD COLUMN detail TEXT;
