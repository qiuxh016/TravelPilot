"use client";

import { useParams } from "next/navigation";
import { TripSharingTool } from "../../../../components/trip-sharing-tool";

export default function TripSharingPage() {
  const tripId = useParams<{ tripId: string }>().tripId;
  return <TripSharingTool tripId={tripId} />;
}
