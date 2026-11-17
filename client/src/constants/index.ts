export const MAX_SLOTS = 8; // Per floor

// Reservation window closes at midnight (00:00 of the reservation date)
export const RESERVATION_CLOSE_HOUR = 23; // 11 PM
export const RESERVATION_CLOSE_MINUTE = 59;

export const ANIMATION_DURATION = 600; // ms

export const COMPANY_NAME = 'Quadrant IT Services';
export const APP_NAME = 'Parking Reservation';
export const LOGIN_TITLE = 'Company Login';

/* Roles. Anyone who is not a plain "employee" can be given a permanently reserved slot. */
export const ROLE_OPTIONS: { value: string; label: string }[] = [
  { value: 'employee', label: 'Employee' },
  { value: 'manager', label: 'Manager' },
  { value: 'director', label: 'Director' },
  { value: 'vp', label: 'Vice President' },
  { value: 'cto', label: 'CTO' },
  { value: 'cfo', label: 'CFO' },
  { value: 'coo', label: 'COO' },
  { value: 'ceo', label: 'CEO' },
  { value: 'admin', label: 'Admin' },
  { value: 'hr', label: 'HR' },
];
export const canHoldSlot = (role?: string | null) => !!role && role !== 'employee';
export const isAdminRole = (role?: string | null) => role === 'hr' || role === 'admin';
export const roleLabel = (role?: string | null) =>
  ROLE_OPTIONS.find(r => r.value === role)?.label ?? (role ? role.toUpperCase() : '');
