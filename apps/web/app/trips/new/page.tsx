"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

import {
  ApiError,
  createTrip,
} from "../../../lib/api";

export default function NewTripPage() {
  const router = useRouter();

  const [destination, setDestination] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [budget, setBudget] = useState("");
  const [paceLevel, setPaceLevel] = useState("3");

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  async function handleSubmit(
    event: FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault();

    setError("");
    setSuccess("");

    if (endDate < startDate) {
      setError("结束日期不能早于开始日期");
      return;
    }

    try {
      setLoading(true);

      const trip = await createTrip({
        destination,
        start_date: startDate,
        end_date: endDate,
        budget: budget ? Number(budget) : null,
        pace_level: Number(paceLevel),
      });

      setSuccess("旅行创建成功");

      router.push(`/trips/${trip.id}`);
    } catch (error) {
      if (error instanceof ApiError) {
        setError(error.message);
      } else if (error instanceof Error) {
        setError(error.message);
      } else {
        setError("创建旅行失败");
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="shell">
      <nav className="nav">
        <a className="brand" href="/trips">
          TravelPilot
        </a>
      </nav>

      <section className="hero">
        <p className="eyebrow">New trip</p>
        <h1>创建旅行</h1>
        <p className="lede">
          填写旅行基本信息。
        </p>
      </section>

      <form
        className="trip-form"
        onSubmit={handleSubmit}
      >
        <label>
          目的地
          <input
            required
            value={destination}
            onChange={(event) =>
              setDestination(event.target.value)
            }
            placeholder="例如：成都"
          />
        </label>

        <label>
          开始日期
          <input
            required
            type="date"
            value={startDate}
            onChange={(event) =>
              setStartDate(event.target.value)
            }
          />
        </label>

        <label>
          结束日期
          <input
            required
            type="date"
            value={endDate}
            onChange={(event) =>
              setEndDate(event.target.value)
            }
          />
        </label>

        <label>
          预算
          <input
            min="0"
            type="number"
            value={budget}
            onChange={(event) =>
              setBudget(event.target.value)
            }
          />
        </label>

        <label>
          旅行节奏：{paceLevel}
          <input
            min="1"
            max="5"
            type="range"
            value={paceLevel}
            onChange={(event) =>
              setPaceLevel(event.target.value)
            }
          />
        </label>

        {error && (
          <p role="alert">
            {error}
          </p>
        )}

        {success && (
          <p>
            {success}
          </p>
        )}

        <button
          className="primary"
          disabled={loading}
          type="submit"
        >
          {loading ? "创建中……" : "创建旅行"}
        </button>
      </form>
    </main>
  );
}