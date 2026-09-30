export interface Page<T> {
  next_cursor: string | null;
  has_more: boolean;
  items?: T[];
}
export interface Asset {
  asset_id: number;
  availability?: string;
  byte_size?: number;
  created_at?: number;
  current_location?: { relative_path: string };
  locations?: {
    relative_path: string;
    source_display_name?: string;
    availability: string;
  }[];
  width?: number;
  height?: number;
  format?: string;
  hit_reason?: string;
  matched_text?: string[];
  taken_time?: { taken_at: string | null; label: string; camera?: string };
  semantic_ready?: boolean;
}
export interface Person {
  person_id: number;
  name: string | null;
  revision: number;
  asset_count: number;
  face_count: number;
  thumbnail_url?: string | null;
}
export interface Place {
  asset_count?: number;
  cover_asset_id?: number | null;
  place_id: number;
  canonical_name: string;
  parent_place_id: number | null;
  kind: string;
  revision: number;
  aliases: { text: string }[];
}
export interface PlaceDetail {
  effective_origin?: string | null;
  areas?: Place[];
  automatic?: {status: string; datum_status: string; version: string; layer: string}[];
  assignment: { revision: number; mode: string; origin?: string } | null;
  place: (Place & { ancestors: Place[] }) | null;
  suggested: {
    candidates?: {
      candidate_id: string;
      local_place_id: number | null;
      display_name: string;
      ancestor_names: string[];
      distance_m: number;
    }[];
    [key: string]: unknown;
  };
  gps_state: string;
}
export interface Face {
  face_id: string;
  asset_id: number;
  mode: string;
  control_revision: number;
  assigned_person_id: number | null;
  assignment_revision: number | null;
  thumbnail_url: string;
}
export interface Operation {
  operation_id: string;
  operation_revision: number;
  operation_type: string;
  status: string;
  created_at?: number;
  changed?: boolean;
  undo_of_operation_id?: string | null;
}
export interface Stage {
  state: string;
  total?: number;
  handled?: number;
  searchable?: number;
  pending?: number;
  [key: string]: unknown;
}
export interface Source {
  source_id: number;
  display_name: string;
  status: string;
  last_successful_scan: number | null;
  progress?: {
    built: boolean;
    stages: Record<string, Stage>;
    search_ready: boolean;
  };
}
export interface Refresh {
  running: boolean;
  accepted?: boolean;
  state: string;
  stages: Record<string, Stage>;
  errors: { code?: string; message?: string; stage?: string }[];
}
export interface Capabilities {
  writable: boolean;
  faces?: Record<string, unknown>;
  ocr?: Record<string, unknown>;
  runtime?: { runtime_backend: string; health_status: string };
  scheduler?: { state: "running" | "paused" };
  indexing?: Record<string, unknown>;
}
export interface Filters {
  source_id: number | null;
  person_ids: number[];
  person_match: "any" | "all";
  place_ids: number[];
  include_descendants: boolean;
  place_state: string;
  availability: string;
  dates: [number, number] | null;
}
export interface SearchResult extends Page<Asset> {
  hits: Asset[];
  search_session_id: string;
  profile_id: string | null;
  total_candidates: number;
  candidates_truncated: boolean;
  state: string;
  pending_semantic?: { count?: number };
  search_projection?: { state?: string };
  taken_time?: { state: string };
}
export interface Mapping {
  old_face_id: string;
  asset_id: number;
  revision: number;
  state: string;
  candidate_state: string;
  control_mode: string;
  thumbnail_url: string;
  candidates?: {
    new_face_id: string;
    revision: number;
    thumbnail_url: string;
    state: string;
  }[];
}
