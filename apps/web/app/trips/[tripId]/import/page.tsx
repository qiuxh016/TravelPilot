"use client";

import { useParams } from "next/navigation";
import { TripImportTool } from "../../../../components/trip-import-tool";

export default function ImportPage() {
  const tripId = useParams<{ tripId: string }>().tripId;
  return <TripImportTool tripId={tripId} />;
}
