"use client";

import { FormEvent, PointerEvent as ReactPointerEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { RouteMap } from "../../components/route-map";
import { TripImportTool } from "../../components/trip-import-tool";
import { AdvisorWhatIf } from "../../components/advisor-what-if";
import { tripOptionLabel, useTripSelection } from "../../components/trip-selection-context";
import {
  AdvisorCandidate, AdvisorDestinationRecommendation, AdvisorMemorySuggestion, AdvisorMessageFeedbackReason, AdvisorMessageFeedbackRating, AdvisorProgressEvent, AdvisorRuntimeStatus, AdvisorSession, AdvisorTaskSnapshot, AdvisorTransportRoute, ApiError, Trip, confirmAdvisorDraft, confirmUserMemorySuggestion, createAdvisorDraft, createAdvisorSession, createUserMemory,
  getAdvisorRunProgress, getAdvisorRuntimeStatus, getAdvisorSession, getStaticMapImage, listAdvisorSessions, listTrips, recommendAdvisorDestinations, sendAdvisorMessage, submitAdvisorMessageFeedback, updateAdvisorSessionContext,
  updateAdvisorChecklistDecision,
  deleteAdvisorSession as removeAdvisorSession,
} from "../../lib/api";

type Mode = "discover" | "destination" | "existing";
type AdvisorStage = "home" | "setup" | "chat";
type AgentTrace = {
  decision_source?: string;
  decision_summary?: string;
  status?: string;
  route_evidence?: string;
  route_source?: string | null;
  transitions?: Array<{ tool?: string; status?: string; error?: string; stage?: string }>;
  stage_statuses?: Array<{ stage?: string; status?: string }>;
  execution_metrics?: { logical_steps?: number; tool_attempts?: number; output_tokens?: number };
  recovery?: { status?: string; interrupted_stage?: string | null; completed_stages?: string[] };
  turn_orchestration?: {
    order?: string[];
    decision_source?: string;
    original_decision_source?: string;
    decision_summary?: string;
    fallback_reason?: string | null;
    decision_history?: Array<{
      eligible_stages?: string[];
      order?: string[];
      decision_source?: string;
      decision_summary?: string;
      fallback_reason?: string | null;
    }>;
  };
};
type AdvisorEvidenceSummary = {
  total: number;
  verified: number;
  simulated: number;
  providers: string[];
};
type AdvisorWeatherForecast = {
  status: "AVAILABLE" | "OUT_OF_RANGE";
  city?: string;
  source: string;
  attribution?: string;
  fetched_at: string;
  message?: string;
  days: Array<{
    date: string;
    description: string;
    weather_code?: number;
    temperature_min_c?: number;
    temperature_max_c?: number;
    precipitation_probability_max?: number;
  }>;
};
type TripActionItem = { key: string; title: string; detail: string; action: "CHAT" | "EDIT_ORIGIN"; action_label: string; prompt: string };
type DraftFollowUpOption = { key: string; title: string; detail: string; action_label: string; prompt: string };
const ADVISOR_FEEDBACK_REASON_LABELS: Record<AdvisorMessageFeedbackReason, string> = {
  MISSED_REQUEST: "没回答我的问题",
  INACCURATE: "信息不准确",
  NOT_ACTIONABLE: "建议不够具体",
  TOO_VERBOSE: "内容太长",
  OTHER: "其他原因",
};

function advisorStageStatusLabel(status?: string) {
  return ({
    SUCCEEDED: "完成",
    NEEDS_USER_INPUT: "等待你补充",
    BLOCKED_DEPENDENCY: "等待前置步骤",
    NOT_EXECUTED: "已就绪但未执行",
    FAILED: "未完成",
    REJECTED: "未完成",
    FALLBACK: "降级处理",
    STEP_LIMIT: "达到步骤上限",
  } as Record<string, string>)[status ?? ""] ?? "处理中";
}

function advisorTurnStageLabel(stage: string) {
  return ({
    SEARCH_WEATHER: "地点与天气",
    ROUTE: "路线查询",
    SCHEDULE: "行程安排",
  } as Record<string, string>)[stage] ?? stage;
}

function readAgentTrace(payload: Record<string, unknown> | null): AgentTrace | null {
  const value = payload?.agent_trace;
  return value && typeof value === "object" ? value as AgentTrace : null;
}

function readAdvisorEvidenceSummary(payload: Record<string, unknown> | null): AdvisorEvidenceSummary | null {
  const value = payload?.verified_candidates;
  if (!Array.isArray(value)) return null;
  const evidence = value.slice(0, 8).filter((candidate): candidate is Record<string, unknown> =>
    Boolean(candidate) && typeof candidate === "object" && !Array.isArray(candidate));
  if (!evidence.length) return null;
  const providers = new Set<string>();
  let verified = 0;
  let simulated = 0;
  for (const candidate of evidence) {
    if (typeof candidate.provider === "string") {
      providers.add(candidate.provider === "amap" ? "高德地图" : candidate.provider === "fake" ? "模拟地图" : candidate.provider.slice(0, 40));
    }
    if (candidate.verification_status === "VERIFIED") verified += 1;
    if (candidate.verification_status === "SIMULATED") simulated += 1;
  }
  return { total: evidence.length, verified, simulated, providers: [...providers] };
}

function readAdvisorWeatherForecast(payload: Record<string, unknown> | null): AdvisorWeatherForecast | null {
  const value = payload?.weather_forecast;
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const forecast = value as Partial<AdvisorWeatherForecast>;
  if ((forecast.status !== "AVAILABLE" && forecast.status !== "OUT_OF_RANGE")
    || typeof forecast.source !== "string" || typeof forecast.fetched_at !== "string"
    || !Array.isArray(forecast.days) || forecast.days.length > 16) return null;
  const days = forecast.days.flatMap((entry) => {
    if (!entry || typeof entry !== "object") return [];
    const day = entry as Partial<AdvisorWeatherForecast["days"][number]>;
    if (typeof day.date !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(day.date)
      || typeof day.description !== "string") return [];
    const boundedNumber = (number: unknown, minimum: number, maximum: number) =>
      typeof number === "number" && Number.isFinite(number)
      && number >= minimum && number <= maximum;
    if (day.weather_code !== undefined && !boundedNumber(day.weather_code, 0, 999)) return [];
    if (day.temperature_min_c !== undefined && !boundedNumber(day.temperature_min_c, -100, 70)) return [];
    if (day.temperature_max_c !== undefined && !boundedNumber(day.temperature_max_c, -100, 70)) return [];
    if (day.precipitation_probability_max !== undefined
      && !boundedNumber(day.precipitation_probability_max, 0, 100)) return [];
    return [day as AdvisorWeatherForecast["days"][number]];
  });
  if (days.length !== forecast.days.length) return null;
  return { ...forecast, days } as AdvisorWeatherForecast;
}

function readAdvisorTask(payload: Record<string, unknown> | null): AdvisorTaskSnapshot | null {
  const value = payload?.agent_task;
  if (!value || typeof value !== "object") return null;
  const task = value as Partial<AdvisorTaskSnapshot>;
  if (typeof task.status !== "string" || typeof task.task_type !== "string"
    || typeof task.turn_number !== "number" || !Array.isArray(task.answers)) return null;
  const question = typeof task.question === "string" ? task.question.trim() : "";
  const answers = task.answers;
  const normalizedQuestion = question.replace(/[？?]+$/, "").trim();
  const hasQuestionShape = /[？?]/.test(question)
    || /(?:吗|是否|要不要|可不可以|行不行|想不想|需不需要)$/.test(normalizedQuestion)
    || /是否|要不要|可不可以|能否|行不行|想不想|需不需要|还是|或者|或|[/／]/.test(normalizedQuestion);
  const asksForAction = /(?:需要我|要我|要不要(?:我|把|将)?|要把|是否(?:要|需要|可以)?(?:我|把|将)?|(?:可不可以|可以|能否|可否)(?:把|将)?|是否需要我)/.test(normalizedQuestion)
    && /作为|加入|纳入|添加|更新|放到|排到|挪到|设为|定为/.test(normalizedQuestion)
    && hasQuestionShape;
  const isTimeProposal = /\d{1,2}[/月-]\d{1,2}日?|\d{1,2}:\d{2}|(?:上午|下午|中午|晚上)?\s*[一二两三四五六七八九十\d]+点|这一天|当天|这个日期|这天/.test(normalizedQuestion)
    || (/第[一二两三四五六七八九十\d]+天/.test(normalizedQuestion) && /放到|排到|挪到|调整到|安排到/.test(normalizedQuestion));
  const actionChoices = asksForAction
    ? isTimeProposal ? [
      { label: "可以，按这个时间继续", answer: "可以，按你刚才建议的日期和时间继续安排" },
      { label: "换个日期或时间", answer: "我想调整刚才建议的日期或时间，先不要按原时间安排" },
      { label: "暂不安排", answer: "暂不安排这项内容" },
    ] : [
      { label: "确认，加入安排", answer: "确认，按当前选择加入安排" },
      { label: "返回调整", answer: "先不确认，我想调整这项安排" },
      { label: "暂不安排", answer: "暂不安排这项内容" },
    ]
    : [];
  const alternatives = /还是|或者|或|[/／]/.test(normalizedQuestion)
    ? normalizedQuestion.split(/还是|或者|或|[/／]/).map((part) => part.trim().replace(/^[，,、：:；;\s]+|[，,、：:；;\s]+$/g, ""))
    : [];
  if (alternatives.length) {
    alternatives[0] = alternatives[0]
      .replace(/^.*?(更倾向于|倾向于|更喜欢|更偏爱|更偏好|偏爱|偏向|更看重|更在意|偏好|更想|想体验|希望去|希望是|希望|想要|想去|选择|选|喜欢)\s*/, "")
      .replace(/^[，,、：:；;\s]+|[，,、：:；;\s]+$/g, "");
    if (alternatives.length === 2) {
      const firstChoices = alternatives[0].split(/[、，,：:]/).map((part) => part.trim()).filter(Boolean);
      if (firstChoices.length > 1) alternatives.splice(0, 1, ...firstChoices);
    }
  }
  const choiceOptions = alternatives.length >= 2 && alternatives.length <= 3
    && alternatives.every((choice) => choice.length > 0 && choice.length <= 40)
    ? alternatives.map((choice) => ({ label: choice, answer: `我选${choice}` }))
    : [];
  const asksToContinue = /要我|需要我|要不要|是否要|是否需要|想不想|需不需要/.test(normalizedQuestion)
    && /排出来|生成|推荐|查询|按日期安排/.test(normalizedQuestion);
  const continuationOptions = asksToContinue ? [
    { label: "继续，生成待确认建议", answer: "好的，继续生成待确认建议" },
    { label: "补充或调整要求", answer: "我想先补充或调整要求" },
    { label: "暂不处理", answer: "暂不处理这项任务" },
  ] : [];
  const asksYesNo = /(?:是否|要不要|可不可以|可以吗|能不能|行不行|好吗|合适吗|合理吗|可行吗|想不想|需不需要|同意吗|确认吗|接受吗|要吗|需要吗|(?:需要我|要我).{0,12}吗)$/.test(normalizedQuestion)
    || /是否(?:合理|合适|可行|需要|确认|同意|接受)/.test(normalizedQuestion);
  const yesNoOptions = asksYesNo ? [
    { label: "是，继续", answer: "是，请继续" },
    { label: "否，先不用", answer: "否，暂时不用" },
  ] : [];
  const generatedOptions = !hasQuestionShape ? [] : actionChoices.length ? actionChoices
    : choiceOptions.length ? choiceOptions
      : continuationOptions.length ? continuationOptions : yesNoOptions;
  const confirmationOptions = Array.isArray(task.confirmation_options) && task.confirmation_options.length
    ? task.confirmation_options
    : generatedOptions;
  return { ...task, confirmation_options: confirmationOptions } as AdvisorTaskSnapshot;
}

function isExplicitDraftSaveConfirmation(content: string): boolean {
  const normalized = content.replace(/[\s，。！？、,.!?；;：:]/g, "");
  if (/(不要|先别|暂不|取消|不用|无需|再看看|没确认|尚未确认)/.test(normalized)) return false;
  const confirms = /(确认|同意|可以|好的|好|就按|直接)/.test(normalized);
  const persists = /(保存|提交|写入|应用到行程|更新正式行程)/.test(normalized);
  return confirms && persists;
}

function candidateIdsForTaskConfirmation(
  task: AdvisorTaskSnapshot | null,
  answer: string,
  candidates: AdvisorCandidate[],
): string[] | null {
  if (!task || answer !== "确认，按当前选择加入安排" || !task.question) return null;
  const question = `${task.goal} ${task.question}`;
  if (!/(?:加入|添加|纳入|作为).{0,16}(?:安排|行程|候选)/.test(question)) return null;
  const normalizedQuestion = question.replace(/[\s（）()]/g, "").toLocaleLowerCase();
  const exactMatches = candidates.filter((candidate) => {
    const name = candidate.name.trim().replace(/[\s（）()]/g, "").toLocaleLowerCase();
    return name.length >= 2 && normalizedQuestion.includes(name);
  });
  const matches = exactMatches.length ? exactMatches : candidates.filter((candidate) => {
    const shortName = candidate.name.split(/[（(]/, 1)[0].trim().replace(/\s/g, "").toLocaleLowerCase();
    return shortName.length >= 2 && normalizedQuestion.includes(shortName);
  });
  return matches.length === 1 ? matches.map((candidate) => candidate.id) : null;
}

function isAffirmativeWriteConfirmation(
  task: AdvisorTaskSnapshot,
  option: { label: string; answer: string },
): boolean {
  const wording = `${task.question ?? ""} ${option.label} ${option.answer}`;
  if (!/(?:写入行程|保存到行程|提交到行程|应用到(?:正式)?行程)/.test(wording)) return false;
  if (/(?:先不|暂不|不写入|不保存|稍后|返回调整|取消|不用)/.test(`${option.label} ${option.answer}`)) return false;
  return /(?:确认|写入|保存|提交|应用|可以|同意)/.test(`${option.label} ${option.answer}`);
}

function candidateIdsForExplicitSelection(content: string, candidates: AdvisorCandidate[]): string[] {
  const match = content.match(/(?:我\s*)?(?:选择|选中|选|就用|就选|加入|添加|纳入)\s*([^，,。；;！？!?]{2,80})/);
  if (!match) return [];
  const choice = match[1].replace(/[\s（）()\[\]【】]/g, "").toLocaleLowerCase();
  if (choice.length < 2 || ["这个", "这家", "它", "方案a", "方案b"].includes(choice)) return [];
  const matches = candidates.filter((candidate) => {
    const name = candidate.name.replace(/[\s（）()\[\]【】]/g, "").toLocaleLowerCase();
    const shortName = candidate.name.split(/[（(]/, 1)[0].replace(/\s/g, "").toLocaleLowerCase();
    return name.includes(choice) || shortName.includes(choice) || choice.includes(name);
  });
  return matches.length === 1 ? [matches[0].id] : [];
}

function readTripActionItems(payload: Record<string, unknown> | null | undefined): TripActionItem[] {
  const value = payload?.trip_action_items;
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    if (!entry || typeof entry !== "object") return [];
    const item = entry as Partial<TripActionItem>;
    return typeof item.key === "string" && typeof item.title === "string"
      && typeof item.detail === "string" && typeof item.action_label === "string"
      && (item.action === "CHAT" || item.action === "EDIT_ORIGIN")
      && typeof item.prompt === "string" && item.prompt.length <= 800
      ? [item as TripActionItem] : [];
  }).slice(0, 12);
}

function readDraftFollowUpOptions(payload: Record<string, unknown> | null): DraftFollowUpOption[] {
  const value = payload?.draft_follow_up_options;
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    if (!entry || typeof entry !== "object") return [];
    const option = entry as Partial<DraftFollowUpOption>;
    return typeof option.key === "string" && typeof option.title === "string"
      && typeof option.detail === "string" && typeof option.action_label === "string"
      && typeof option.prompt === "string" && option.prompt.length <= 800
      ? [option as DraftFollowUpOption] : [];
  }).slice(0, 4);
}

function readAdvisorRoute(payload: Record<string, unknown> | null): AdvisorTransportRoute | null {
  const value = payload?.transport_route;
  if (!value || typeof value !== "object") return null;
  const route = value as Partial<AdvisorTransportRoute>;
  const validPath = (points: unknown): points is [number, number][] => Array.isArray(points)
    && points.length <= 500
    && points.every((point) => Array.isArray(point) && point.length === 2
      && typeof point[0] === "number" && Number.isFinite(point[0]) && point[0] >= -90 && point[0] <= 90
      && typeof point[1] === "number" && Number.isFinite(point[1]) && point[1] >= -180 && point[1] <= 180);
  return (route.status === "VERIFIED" || route.status === "SIMULATED")
    && (route.mode === "DRIVING" || route.mode === "WALKING" || route.mode === "TRANSIT") && typeof route.origin_name === "string"
    && typeof route.destination_name === "string" && typeof route.source === "string"
    && typeof route.retrieved_at === "string" && typeof route.distance_m === "number"
    && Number.isFinite(route.distance_m) && route.distance_m > 0
    && typeof route.duration_min === "number" && Number.isFinite(route.duration_min)
    && route.duration_min > 0 && validPath(route.points) && route.points.length === 2
    && validPath(route.polyline)
    ? route as AdvisorTransportRoute : null;
}

function progressTitle(event: AdvisorProgressEvent): string {
  if (event.phase === "turn_stage") {
    const stage = ({ SEARCH_WEATHER: "地点与天气", ROUTE: "路线查询", SCHEDULE: "行程安排" } as Record<string, string>)[event.metadata.tool ?? ""] ?? "旅行任务步骤";
    if (event.status === "STAGE_STARTED") return `正在进行${stage}`;
    if (event.status === "SUCCEEDED") return `${stage}已完成`;
    if (event.status === "NEEDS_USER_INPUT") return `${stage}需要你补充信息`;
    if (event.status === "FAILED" || event.status === "REJECTED") return `${stage}暂未完成`;
  }
  if (event.status === "TURN_STARTED" || event.status === "TURN_HEARTBEAT") return "顾问正在推进这条任务";
  if (event.status === "TURN_SUCCEEDED") return "顾问已完成这轮任务";
  if (event.status === "TURN_FAILED") return "这轮任务遇到错误；可以检查提示后重试";
  if (event.status === "TURN_INTERRUPTED") return "这轮任务似乎中断了；重发同一条消息可安全恢复";
  if (event.status === "ACTING") {
    return event.metadata.tool === "get_distance_matrix" ? "正在核对地点间交通"
      : event.metadata.tool === "get_trip_route" ? "正在查询路线"
      : event.metadata.tool === "get_trip_weather" ? "正在查询旅行天气"
      : event.metadata.tool === "search_places" ? "正在搜索地点"
      : event.metadata.tool === "build_schedule_draft" ? "正在安排时间" : "正在处理下一步";
  }
  if (event.status === "VERIFIED") return "已核验一步结果";
  if (event.status === "FAILED") return "这一步暂时失败，顾问正在检查恢复方式";
  if (event.status === "REJECTED") return "发现结果不满足校验，顾问正在调整";
  if (event.status === "NEEDS_USER_INPUT") return "顾问需要你补充信息";
  if (event.status === "SUCCEEDED") return "顾问已完成当前查询";
  if (event.status === "OBSERVING_AFTER_FAILURE") return "正在根据服务反馈调整下一步";
  if (event.status === "OBSERVING_AFTER_REJECTION") return "正在根据校验结果调整下一步";
  return event.phase === "schedule" ? "正在准备行程安排" : "正在了解旅行信息";
}

async function advisorMessageFingerprint(content: string): Promise<string | null> {
  try {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(content));
    return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
  } catch {
    return null;
  }
}

function pendingMessageStorageKey(sessionId: string) {
  return `travelpilot-advisor-pending:${sessionId}`;
}

function advisorTaskLabel(task: AdvisorTaskSnapshot) {
  const kind = ({
    DISCOVERY: "目的地与地点探索", SCHEDULE: "行程安排", SEARCH: "地点搜索",
    MEAL: "用餐规划", LODGING: "住宿规划", TRANSPORT: "交通路线",
    WEATHER: "天气查询", PREPARATION: "出行准备", MEMORY: "旅行记录", GENERAL: "旅行咨询",
  } as Record<string, string>)[task.task_type] ?? "旅行任务";
  const status = ({ WAITING_USER: "等你补充", PROPOSED: "草案待确认", COMPLETED: "本轮完成", PARTIAL: "部分完成", CANCELLED: "已取消", STALE: "信息已变化", FAILED: "遇到问题" } as Record<string, string>)[task.status] ?? "处理中";
  const nextStep = task.status === "WAITING_USER" ? null
    : task.status === "PROPOSED" ? "先检查草案和变更；只有你确认后才会保存。"
    : task.status === "FAILED" ? "检查上方说明后，可补充条件或调整问题再试。"
    : task.status === "PARTIAL" ? "先查看已完成部分和未解决事项，再决定是否继续。"
    : task.status === "STALE" ? "旅行信息已变化，请刷新上下文后重新核对结果。"
    : task.status === "CANCELLED" ? "这项咨询已结束，没有因此修改正式行程。"
    : ({
      DISCOVERY: "浏览目的地与地点候选；告诉顾问你的偏好，可继续缩小范围。",
      SCHEDULE: "检查行程草案、交通与空档；确认前不会写入正式行程。",
      SEARCH: "比较地点候选；选择后再补充日期和时间，避免替你猜安排。",
      MEAL: "比较餐饮候选并选择餐次；不会把餐馆当作游览景点安排。",
      LODGING: "比较住宿备选；加入清单不代表预订，也不会占用每日游览时间。",
      TRANSPORT: "核对路线起终点、交通方式和数据来源；路线查询不会改动行程。",
      WEATHER: "查看标注日期与来源的天气信息；预报范围外不会被当作确定天气。",
      PREPARATION: "结合天气和行程检查穿衣、雨具与室内备选建议。",
      MEMORY: "检查这条旅行记录或记忆建议；保存前由你确认。",
      GENERAL: "可以继续追问旅行相关问题，或告诉顾问接下来要处理的事项。",
    } as Record<AdvisorTaskSnapshot["task_type"], string>)[task.task_type];
  return { kind, status, nextStep };
}

function AdvisorRouteResult({ route }: { route: AdvisorTransportRoute }) {
  const [imageUrl, setImageUrl] = useState("");
  const [mapError, setMapError] = useState("");
  const routeSignature = useMemo(() => JSON.stringify(route), [route]);

  useEffect(() => {
    let active = true;
    let objectUrl = "";
    const currentRoute = JSON.parse(routeSignature) as AdvisorTransportRoute;
    setImageUrl("");
    setMapError("");
    if (currentRoute.status !== "VERIFIED" || currentRoute.polyline.length < 2 || currentRoute.points.length !== 2) {
      return () => { active = false; };
    }
    void getStaticMapImage(
      currentRoute.points.map(([latitude, longitude]) => ({ latitude, longitude })),
      0,
      currentRoute.polyline.slice(0, 500),
    ).then((url) => {
      objectUrl = url;
      if (active) setImageUrl(url);
      else URL.revokeObjectURL(url);
    }).catch((error: unknown) => {
      if (active) setMapError(error instanceof Error ? error.message : "路线地图暂时无法加载");
    });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [routeSignature]);

  const modeLabel = route.mode === "WALKING" ? "步行" : route.mode === "TRANSIT" ? "公交/地铁" : "驾车";
  return <section className="advisor-route-result" aria-label={`${modeLabel}路线结果`}>
    <div className="advisor-route-result-heading"><strong>{route.origin_name}</strong><span aria-hidden="true">→</span><strong>{route.destination_name}</strong></div>
    <div className="advisor-route-result-stats">
      <span>{modeLabel}约 {(route.distance_m / 1000).toFixed(1)} 公里</span>
      <span>预计 {route.duration_min} 分钟</span>
      <span>{route.status === "VERIFIED" ? "地图核验" : "本地模拟"}</span>
    </div>
    {route.segments?.length ? <ol className="advisor-route-result-steps">{route.segments.slice(0, 12).map((segment, index) => <li key={`${index}-${segment.mode}`}>{segment.label}</li>)}</ol> : null}
    {imageUrl ? <img className="advisor-route-result-map" src={imageUrl} alt={`${route.origin_name}到${route.destination_name}的高德${modeLabel}路线`} /> : mapError ? <small role="status">路线数据已核验；底图暂不可用：{mapError}</small> : route.status === "SIMULATED" ? <small>本地模拟路线，不代表真实道路导航。</small> : null}
    <small>来源：{route.source} · 查询于 {route.retrieved_at.replace("T", " ").slice(0, 16)}。耗时不含实时路况变化。</small>
  </section>;
}

function splitTags(value: string) {
  return [...new Set(value.split(/[，,、;；]/).map((item) => item.trim()).filter(Boolean))].slice(0, 10);
}

function planningCategoryLabel(category?: string, mealType?: string | null) {
  if (category === "FOOD") return ({ BREAKFAST: "早餐", LUNCH: "午餐", DINNER: "晚餐", SNACK: "小吃 / 加餐" } as Record<string, string>)[mealType ?? ""] ?? "餐饮";
  if (category === "LODGING") return "住宿";
  if (category === "ATTRACTION") return "游玩";
  return "";
}

function checklistItemKey(item: { kind: string; day: string; meal_type?: string; from?: string; to?: string }) {
  if (item.meal_type) return `${item.kind}:${item.day}:${item.meal_type}`;
  if (item.kind === "OPEN_GAP") return `${item.kind}:${item.day}:GAP:${(item.from ?? "").replace(":", "")}-${(item.to ?? "").replace(":", "")}`;
  return `${item.kind}:${item.day}`;
}

function schedulesForSession(session: AdvisorSession) {
  const schedules: Record<string, { day: string; start_time: string }> = {};
  for (const stop of session.context.current_plan?.stops ?? []) {
    schedules[stop.candidate_id] = { day: stop.day, start_time: stop.start_time.slice(0, 5) };
  }
  for (const candidate of session.candidates) {
    if (!candidate.explicit_day && !candidate.explicit_start_time) continue;
    const current = schedules[candidate.id] ?? { day: "", start_time: "" };
    schedules[candidate.id] = {
      day: candidate.explicit_day ?? current.day,
      start_time: candidate.explicit_start_time ?? current.start_time,
    };
  }
  for (const stop of session.draft?.stops ?? []) {
    schedules[stop.candidate_id] = { day: stop.day, start_time: stop.start_time.slice(0, 5) };
  }
  return schedules;
}

export default function AdvisorPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { selectedTripId } = useTripSelection();
  const [mode, setMode] = useState<Mode>("destination");
  const [stage, setStage] = useState<AdvisorStage>("home");
  const [setupChoice, setSetupChoice] = useState<"trip" | "ideas" | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [chatRatio, setChatRatio] = useState(50);
  const [chatMessagesHeight, setChatMessagesHeight] = useState(620);
  const [advisorChatPanelHeight, setAdvisorChatPanelHeight] = useState<number | null>(null);
  const [chatExpanded, setChatExpanded] = useState(false);
  const [contextEditorOpen, setContextEditorOpen] = useState(false);
  const [tripActionsExpanded, setTripActionsExpanded] = useState(false);
  const [dismissedDraftFollowUps, setDismissedDraftFollowUps] = useState<string[]>([]);
  const [destination, setDestination] = useState("");
  const [explorationCity, setExplorationCity] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [budget, setBudget] = useState("");
  const [pace, setPace] = useState(3);
  const [preference, setPreference] = useState("");
  const [origin, setOrigin] = useState("");
  const [companionsText, setCompanionsText] = useState("");
  const [constraintsText, setConstraintsText] = useState("");
  const [tripId, setTripId] = useState("");
  const [trips, setTrips] = useState<Trip[]>([]);
  const [sessions, setSessions] = useState<AdvisorSession[]>([]);
  const [runtimeStatus, setRuntimeStatus] = useState<AdvisorRuntimeStatus | null>(null);
  const [active, setActive] = useState<AdvisorSession | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [schedules, setSchedules] = useState<Record<string, { day: string; start_time: string }>>({});
  const [autoPreviewRevision, setAutoPreviewRevision] = useState(0);
  const [autoPreviewPending, setAutoPreviewPending] = useState(false);
  const lastAutoPreviewFingerprint = useRef("");
  const [mapDay, setMapDay] = useState("");
  const [candidateFilter, setCandidateFilter] = useState<"AUTO" | "ALL" | "ATTRACTION" | "FOOD" | "LODGING">("AUTO");
  const [candidateMapOpen, setCandidateMapOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [retryableMessage, setRetryableMessage] = useState<{ sessionId: string; content: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [deletingSessionId, setDeletingSessionId] = useState<string | null>(null);
  const [savingMemoryMessageId, setSavingMemoryMessageId] = useState<string | null>(null);
  const [feedbackSavingMessageId, setFeedbackSavingMessageId] = useState<string | null>(null);
  const [feedbackReasonMessageId, setFeedbackReasonMessageId] = useState<string | null>(null);
  const [savedMemoryMessageIds, setSavedMemoryMessageIds] = useState<string[]>([]);
  const [dismissedMemorySuggestionIds, setDismissedMemorySuggestionIds] = useState<string[]>([]);
  const [importOpen, setImportOpen] = useState(false);
  const [whatIf, setWhatIf] = useState<{ tripId: string; eventType: string; prefill?: { condition?: string; delay_min?: number } } | null>(null);
  const [showHandledAttention, setShowHandledAttention] = useState(false);
  const [selectedAttention, setSelectedAttention] = useState<{ sessionId: string; itemKey: string } | null>(null);
  const closeAdvisorImport = useCallback(() => setImportOpen(false), []);
  const [agentActivity, setAgentActivity] = useState("");
  const [agentProgress, setAgentProgress] = useState<AdvisorProgressEvent[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [memoryConflictMessage, setMemoryConflictMessage] = useState("");
  const messagesRef = useRef<HTMLDivElement | null>(null);
  const advisorColumnsRef = useRef<HTMLDivElement | null>(null);
  const advisorChatPanelRef = useRef<HTMLElement | null>(null);
  const resizingAdvisorColumnsRef = useRef(false);
  const resizingAdvisorMessagesRef = useRef(false);
  const pendingMessageRequestRef = useRef<{ sessionId: string; content: string; idempotencyKey: string } | null>(null);
  const pendingDestinationRequestRef = useRef<{
    sessionId: string; fingerprint: string; idempotencyKey: string;
  } | null>(null);
  const pendingDraftRequestRef = useRef<{ fingerprint: string; key: string } | null>(null);
  const progressAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const savedRatio = Number(window.localStorage.getItem("travelpilot-advisor-chat-ratio"));
    if (Number.isFinite(savedRatio) && savedRatio >= 35 && savedRatio <= 65) {
      setChatRatio(savedRatio);
    }
    const savedHeight = Number(window.localStorage.getItem("travelpilot-advisor-message-height"));
    if (Number.isFinite(savedHeight) && savedHeight >= 360 && savedHeight <= 1000) {
      setChatMessagesHeight(savedHeight);
    } else if (window.innerWidth <= 1100) {
      setChatMessagesHeight(500);
    }
  }, []);

  useEffect(() => {
    const panel = advisorChatPanelRef.current;
    if (!panel || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setAdvisorChatPanelHeight(Math.round(entry.contentRect.height));
    });
    observer.observe(panel);
    return () => observer.disconnect();
  }, [stage, active?.id]);

  function resizeAdvisorMessages(event: ReactPointerEvent<HTMLDivElement>) {
    if (!resizingAdvisorMessagesRef.current || !messagesRef.current) return;
    const bounds = messagesRef.current.getBoundingClientRect();
    setChatMessagesHeight(Math.max(360, Math.min(1000, Math.round(event.clientY - bounds.top))));
  }

  function finishAdvisorMessageResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (!resizingAdvisorMessagesRef.current) return;
    resizingAdvisorMessagesRef.current = false;
    window.localStorage.setItem("travelpilot-advisor-message-height", String(chatMessagesHeight));
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  function resizeAdvisorColumns(event: ReactPointerEvent<HTMLDivElement>) {
    if (!resizingAdvisorColumnsRef.current || !advisorColumnsRef.current) return;
    const bounds = advisorColumnsRef.current.getBoundingClientRect();
    if (!bounds.width) return;
    const ratio = Math.round(((event.clientX - bounds.left) / bounds.width) * 100);
    setChatRatio(Math.max(35, Math.min(65, ratio)));
  }

  function finishAdvisorColumnResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (!resizingAdvisorColumnsRef.current) return;
    resizingAdvisorColumnsRef.current = false;
    window.localStorage.setItem("travelpilot-advisor-chat-ratio", String(chatRatio));
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  function draftRequestKey(
    sessionId: string,
    candidateIds: string[],
    draftSchedules: { candidate_id: string; day: string; start_time: string }[],
    allowMemoryConflicts: boolean,
  ) {
    const fingerprint = JSON.stringify({
      sessionId,
      candidate_ids: candidateIds,
      schedules: draftSchedules,
      allow_profile_memory_conflicts: allowMemoryConflicts,
    });
    const current = pendingDraftRequestRef.current;
    if (current?.fingerprint === fingerprint) return current.key;
    const key = crypto.randomUUID();
    pendingDraftRequestRef.current = { fingerprint, key };
    return key;
  }

  function finishDraftRequest(key: string) {
    if (pendingDraftRequestRef.current?.key === key) pendingDraftRequestRef.current = null;
  }

  async function saveExplicitMemory(
    messageId: string,
    content: string,
    constraint_type: "PREFERENCE" | "HARD_LIMIT",
  ) {
    const text = content.trim().slice(0, 1000);
    if (text.length < 2) return;
    setSavingMemoryMessageId(messageId);
    setError("");
    try {
      await createUserMemory({ category: "GENERAL", text, constraint_type });
      setSavedMemoryMessageIds((current) => [...current, messageId]);
      setNotice("已保存这条明确表达的偏好。是否用于后续规划由“账户与隐私”的记忆开关控制；顾问不会自动推断其他个人信息。");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "偏好保存失败，请重试。");
    } finally {
      setSavingMemoryMessageId(null);
    }
  }

  async function saveSuggestedMemory(
    messageId: string,
    suggestion: AdvisorMemorySuggestion,
    constraint_type: "PREFERENCE" | "HARD_LIMIT",
  ) {
    setSavingMemoryMessageId(messageId);
    setError("");
    try {
      await confirmUserMemorySuggestion({ ...suggestion, constraint_type });
      setSavedMemoryMessageIds((current) => [...current, messageId]);
      setNotice("已按你的确认保存偏好记忆；后续是否使用仍由账户记忆开关控制。");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "偏好保存失败，请重试。");
    } finally {
      setSavingMemoryMessageId(null);
    }
  }

  async function saveAdvisorMessageFeedback(
    messageId: string,
    rating: AdvisorMessageFeedbackRating,
    reason?: AdvisorMessageFeedbackReason,
  ) {
    if (!active) return;
    const sessionId = active.id;
    setFeedbackSavingMessageId(messageId);
    setError("");
    try {
      const feedback = await submitAdvisorMessageFeedback(sessionId, messageId, rating, reason);
      const patchMessage = (item: AdvisorSession) => item.id !== sessionId ? item : {
        ...item,
        messages: item.messages.map((entry) => entry.id !== messageId ? entry : {
          ...entry,
          payload: {
            ...(entry.payload ?? {}),
            advisor_feedback: {
              rating: feedback.rating,
              reason: feedback.reason,
              submitted_at: feedback.submitted_at,
            },
          },
        }),
      };
      setActive((current) => current ? patchMessage(current) : current);
      setSessions((current) => current.map(patchMessage));
      setFeedbackReasonMessageId(null);
      setNotice("已记录这条回复的评价，可随时修改。你的评价不会用于自动保存个人记忆。");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "反馈没有保存，请重试。");
    } finally {
      setFeedbackSavingMessageId(null);
    }
  }

  function handleDraftMemoryConflict(reason: unknown): boolean {
    if (!(reason instanceof ApiError) || reason.status !== 409
      || !reason.message.startsWith("PROFILE_MEMORY_CONFLICT：")) return false;
    setMemoryConflictMessage(reason.message.slice("PROFILE_MEMORY_CONFLICT：".length));
    setError("");
    return true;
  }

  useEffect(() => () => progressAbortRef.current?.abort(), []);

  const refresh = useCallback(async () => {
    const [nextTrips, nextSessions] = await Promise.all([listTrips(), listAdvisorSessions()]);
    setTrips(nextTrips);
    setSessions(nextSessions);
    const targetSession = searchParams.get("sessionId");
    const targetTrip = searchParams.get("tripId");
    const found = nextSessions.find((item) => item.id === targetSession);
    if (found) {
      setStage("chat");
      setActive(found);
      setExplorationCity("");
      setTripId(found.trip_id || "");
      setSelected(found.context?.selected_candidate_ids ?? []);
      setSchedules(schedulesForSession(found));
      setDestination(found.destination || "");
      setStartDate(found.start_date || "");
      setEndDate(found.end_date || "");
      setBudget(found.budget == null ? "" : String(found.budget));
      setPace(found.pace_level);
      setPreference(found.preference_text || "");
      setOrigin(found.context?.planning_inputs?.origin ?? "");
      setCompanionsText((found.context?.planning_inputs?.companions ?? []).join("、"));
      setConstraintsText((found.context?.planning_inputs?.hard_constraints ?? []).join("、"));
      // The pending request body stays server-side in the saved user message.
      // sessionStorage contains only its digest and idempotency key; use both
      // to offer a safe same-key retry after a reload or browser restart.
      try {
        const raw = sessionStorage.getItem(pendingMessageStorageKey(found.id));
        const pending = raw ? JSON.parse(raw) as {
          idempotencyKey?: string; fingerprint?: string;
        } : null;
        const lastMessage = found.messages.at(-1);
        if (pending?.idempotencyKey && pending.fingerprint && lastMessage?.role === "user") {
          const fingerprint = await advisorMessageFingerprint(lastMessage.content);
          if (fingerprint === pending.fingerprint) {
            setRetryableMessage({ sessionId: found.id, content: lastMessage.content });
            setError("检测到上次顾问请求尚未收到回复。可以使用同一请求编号安全恢复。");
          }
        }
      } catch { /* Invalid or unavailable browser storage cannot block session loading. */ }
    }
    if (targetTrip) {
      setTripId(targetTrip);
      setMode("existing");
      setSetupChoice("trip");
      setStage("setup");
    } else if (!targetSession && selectedTripId) {
      setTripId(selectedTripId);
    }
  }, [searchParams, selectedTripId]);

  useEffect(() => {
    refresh().catch((reason: Error) => setError(reason.message));
  }, [refresh]);

  useEffect(() => {
    const messagesElement = messagesRef.current;
    if (stage !== "chat" || !active?.messages.length || !messagesElement) return;
    messagesElement.scrollTo({ top: messagesElement.scrollHeight, behavior: "smooth" });
    messagesElement.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [active?.id, active?.messages.length, stage]);

  useEffect(() => {
    if (stage !== "setup" || searchParams.has("sessionId") || searchParams.has("tripId")) return;
    setTripId(selectedTripId);
    setMode(selectedTripId ? "existing" : "discover");
    setSetupChoice(selectedTripId ? "trip" : "ideas");
  }, [selectedTripId, stage, searchParams]);

  useEffect(() => {
    getAdvisorRuntimeStatus().then(setRuntimeStatus).catch(() => setRuntimeStatus({
      llm_status: "UNAVAILABLE", model_name: null, output_token_limit: 0,
      upstream_status: "CHECKED_PER_REQUEST",
    }));
  }, []);

  const selectedTrip = trips.find((trip) => trip.id === tripId);
  const latestAssistantPayload = [...(active?.messages ?? [])].reverse().find((item) => item.role === "assistant")?.payload;
  const latestTripActionPayload = [...(active?.messages ?? [])].reverse().find((item) =>
    item.role === "assistant" && Array.isArray(item.payload?.trip_action_items),
  )?.payload;
  const tripActionItems = readTripActionItems(latestTripActionPayload);
  const mealCoveredInCurrentWork = (day: string, meal: "午餐" | "晚餐") => {
    const [windowStart, windowEnd] = meal === "午餐" ? [11 * 60, 14 * 60] : [17 * 60, 20 * 60];
    const scheduledFood: {
      category?: string; day?: string; start_time?: string; end_time?: string; duration_min?: number;
    }[] = [
      ...(active?.draft?.stops ?? []).filter((stop) => selected.includes(stop.candidate_id)).map((stop) => ({
        category: stop.planning_category, day: stop.day, start_time: stop.start_time,
        end_time: stop.end_time,
      })),
      ...(active?.candidates ?? [])
        .filter((candidate) => selected.includes(candidate.id))
        .map((candidate) => ({
          category: candidate.planning_category,
          day: schedules[candidate.id]?.day,
          start_time: schedules[candidate.id]?.start_time,
          duration_min: candidate.duration_min,
        })),
    ];
    return scheduledFood.some((item) => {
      if (item.category !== "FOOD" || item.day !== day || !item.start_time) return false;
      const [hour, minute] = item.start_time.slice(0, 5).split(":").map(Number);
      const start = hour * 60 + minute;
      if (!Number.isFinite(start)) return false;
      const [endHour, endMinute] = item.end_time
        ? item.end_time.slice(0, 5).split(":").map(Number)
        : [Number.NaN, Number.NaN];
      const end = Number.isFinite(endHour) && Number.isFinite(endMinute)
        ? endHour * 60 + endMinute
        : start + Math.max(0, item.duration_min ?? 0);
      return end === start
        ? start >= windowStart && start < windowEnd
        : Math.max(0, Math.min(end, windowEnd) - Math.max(start, windowStart)) >= 30;
    });
  };
  const lodgingSelectedInCurrentWork = Boolean(
    active?.draft?.lodging?.some((item) => selected.includes(item.candidate_id))
    || active?.candidates.some((candidate) =>
      candidate.planning_category === "LODGING" && selected.includes(candidate.id),
    ),
  );
  const uniqueTripActionItems = tripActionItems.flatMap((item) => {
    if (item.key === "LODGING" && lodgingSelectedInCurrentWork) return [];
    const meal = item.key === "MEAL:午餐" ? "午餐" : item.key === "MEAL:晚餐" ? "晚餐" : null;
    if (!meal) return [item];
    const dates = [...new Set(item.prompt.match(/20\d{2}-\d{2}-\d{2}/g) ?? [])];
    if (!dates.length) return [item];
    const missingDates = dates.filter((day) => !mealCoveredInCurrentWork(day, meal));
    if (!missingDates.length) return [];
    return [{
      ...item,
      title: `有 ${missingDates.length} 天${meal}还没安排`,
      detail: `日期：${missingDates.join("、")}。已选地点会在预览更新后重新核验。`,
      prompt: `请为${missingDates.join("、")}推荐${meal}地点，只展示餐饮候选；我选择后再确认具体时间。`,
    }];
  });
  const visibleTripActionItems = tripActionsExpanded ? uniqueTripActionItems : uniqueTripActionItems.slice(0, 5);
  const activePlanningCategory = typeof latestAssistantPayload?.planning_category === "string" ? latestAssistantPayload.planning_category : null;
  const latestVerifiedCategories = new Set(
    (Array.isArray(latestAssistantPayload?.verified_candidates) ? latestAssistantPayload.verified_candidates : [])
      .flatMap((candidate) => {
        if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) return [];
        const category = (candidate as Record<string, unknown>).planning_category;
        return category === "ATTRACTION" || category === "FOOD" || category === "LODGING" ? [category] : [];
      }),
  );
  const hasMixedRecommendation = latestVerifiedCategories.size > 1;
  const hasNewSelectedCandidate = Boolean(active?.candidates.some((candidate) => selected.includes(candidate.id) && !candidate.in_current_plan));
  const advisorStep = active?.draft ? 3 : hasNewSelectedCandidate ? 3 : (active?.candidates.length || (active?.messages.length ?? 0) > 1) ? 2 : 1;
  const effectiveCandidateFilter = candidateFilter === "AUTO"
    ? hasMixedRecommendation ? null : activePlanningCategory
    : candidateFilter === "ALL" ? null : candidateFilter;
  const activeCandidateDomain = effectiveCandidateFilter;
  const showingMixedRecommendation = hasMixedRecommendation && !activeCandidateDomain;
  const displayedCandidates = active?.candidates.filter((candidate) =>
    !effectiveCandidateFilter || candidate.planning_category === effectiveCandidateFilter || (!candidate.planning_category && effectiveCandidateFilter === "ATTRACTION")
  ) ?? [];
  const foodSelectionCount = active?.candidates.filter((candidate) => candidate.planning_category === "FOOD" && selected.includes(candidate.id)).length ?? 0;
  const lodgingSelectionCount = active?.candidates.filter((candidate) => candidate.planning_category === "LODGING" && selected.includes(candidate.id)).length ?? 0;
  const attractionSelectionCount = selected.length - foodSelectionCount - lodgingSelectionCount;
  const candidateCount = (category: "ATTRACTION" | "FOOD" | "LODGING") => active?.candidates.filter((candidate) => (candidate.planning_category ?? "ATTRACTION") === category).length ?? 0;
  const destinationRecommendations = active?.messages.flatMap((item) => {
    const value = item.payload?.recommendations;
    return Array.isArray(value) ? value as AdvisorDestinationRecommendation[] : [];
  }) ?? [];
  const latestDestinationPayload = [...(active?.messages ?? [])].reverse().find(
    (item) => item.role === "assistant" && item.payload?.event === "DESTINATION_RECOMMENDATIONS",
  )?.payload;
  const destinationSearchCount = typeof latestDestinationPayload?.search_count === "number"
    ? latestDestinationPayload.search_count : null;
  const destinationSearchScopeComplete = latestDestinationPayload?.search_scope_complete === true;
  const draftSelectionChanged = Boolean(active?.draft && (
    active.draft.selected_candidate_ids.length !== selected.length ||
    active.draft.selected_candidate_ids.some((id) => !selected.includes(id))
  ));
  const draftScheduleChanged = Boolean(active?.draft && active.draft.stops.some((stop) => {
    const choice = schedules[stop.candidate_id];
    return !choice?.day || !choice.start_time || choice.day !== stop.day || choice.start_time !== stop.start_time.slice(0, 5);
  }));
  const draftIsStale = Boolean(active?.draft_stale || draftSelectionChanged || draftScheduleChanged);

  async function startSession(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true); setError(""); setNotice("");
    try {
      const created = await createAdvisorSession({
        mode,
        ...(mode === "existing" ? { trip_id: tripId } : {
          destination: destination.trim(),
          ...(startDate && endDate ? { start_date: startDate, end_date: endDate } : {}),
          budget: budget ? Number(budget) : null,
        }),
        pace_level: pace,
        preference_text: preference.trim(),
        origin: origin.trim(), companions: splitTags(companionsText), hard_constraints: splitTags(constraintsText),
      });
      setActive(created); setSelected(created.context?.selected_candidate_ids ?? []);
      setExplorationCity("");
      setTripId(created.trip_id || "");
      setDestination(created.destination || ""); setStartDate(created.start_date || ""); setEndDate(created.end_date || "");
      setBudget(created.budget == null ? "" : String(created.budget)); setPace(created.pace_level); setPreference(created.preference_text || "");
      setNotice("会话已保存，可以开始聊地点和偏好。");
      setStage("chat");
      setHistoryOpen(false);
      router.replace(`/advisor?sessionId=${created.id}`);
      setSessions(await listAdvisorSessions());
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }

  async function saveSessionContext(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!active) return;
    if (Boolean(startDate) !== Boolean(endDate)) {
      setError("开始和结束日期需要一起填写。");
      return;
    }
    setBusy(true); setError(""); setNotice("");
    try {
      const updated = await updateAdvisorSessionContext(active.id, {
        destination: destination.trim(), start_date: startDate || null, end_date: endDate || null,
        budget: budget ? Number(budget) : null, pace_level: pace, preference_text: preference.trim(),
        origin: origin.trim(), companions: splitTags(companionsText), hard_constraints: splitTags(constraintsText),
      });
      setActive(updated);
      setSessions((current) => [updated, ...current.filter((item) => item.id !== updated.id)]);
      setNotice("旅行条件已保存；旧草案如有变化需要重新生成。");
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }

  async function submitMessage(
    event?: FormEvent<HTMLFormElement>,
    preset?: string,
    selectionOverride?: string[],
  ): Promise<boolean> {
    event?.preventDefault();
    if (!active) return false;
    const content = (preset ?? message).trim();
    if (!content) return false;
    const explicitCandidateIds = selectionOverride
      ? selectionOverride.filter((id) => !selected.includes(id))
      : candidateIdsForExplicitSelection(content, active.candidates);
    const requestSelection = explicitCandidateIds.length
      ? [...new Set([...selected, ...explicitCandidateIds])]
      : selectionOverride ?? selected;
    const newlySelectedCandidates = active.candidates.filter((candidate) => explicitCandidateIds.includes(candidate.id));
    if (active.draft && isExplicitDraftSaveConfirmation(content)) {
      setMessage("");
      if (draftIsStale) {
        setError("草案所依据的地点、时间或旅行条件已变化，请先点击“更新预览”，再确认保存。");
        return false;
      }
      await confirmDraft();
      return true;
    }
    const fingerprint = await advisorMessageFingerprint(content);
    let storedRequest: { idempotencyKey?: string; fingerprint?: string } | null = null;
    try {
      const value = sessionStorage.getItem(pendingMessageStorageKey(active.id));
      storedRequest = value ? JSON.parse(value) as { idempotencyKey?: string; fingerprint?: string } : null;
    } catch { storedRequest = null; }
    const pendingRequest = pendingMessageRequestRef.current;
    const isSamePendingRequest = pendingRequest?.sessionId === active.id && pendingRequest.content === content;
    const isStoredRetry = Boolean(
      fingerprint && storedRequest?.fingerprint === fingerprint && storedRequest.idempotencyKey,
    );
    const idempotencyKey = pendingRequest?.sessionId === active.id && pendingRequest.content === content
      ? pendingRequest.idempotencyKey
      : fingerprint && storedRequest?.fingerprint === fingerprint && storedRequest.idempotencyKey
        ? storedRequest.idempotencyKey
        : crypto.randomUUID();
    pendingMessageRequestRef.current = { sessionId: active.id, content, idempotencyKey };
    if (fingerprint) {
      try {
        sessionStorage.setItem(pendingMessageStorageKey(active.id), JSON.stringify({ idempotencyKey, fingerprint }));
      } catch { /* The in-memory retry key still protects this page session. */ }
    }
    progressAbortRef.current?.abort();
    const progressAbort = new AbortController();
    progressAbortRef.current = progressAbort;
    setAgentProgress([]);
    setBusy(true); setAgentActivity("正在理解你的目标并读取当前旅行上下文…"); setError(""); setNotice("");
    if (explicitCandidateIds.length) {
      setSelected(requestSelection);
      setNotice(`正在把${newlySelectedCandidates.map((candidate) => candidate.name).join("、")}加入本次选择…`);
    }
    const pollProgress = async () => {
      while (!progressAbort.signal.aborted) {
        try {
          const events = await getAdvisorRunProgress(active.id, idempotencyKey, progressAbort.signal);
          if (!progressAbort.signal.aborted) setAgentProgress(events);
        } catch {
          if (progressAbort.signal.aborted) return;
        }
        await new Promise<void>((resolve) => window.setTimeout(resolve, 700));
      }
    };
    void pollProgress();
    try {
      const updated = await sendAdvisorMessage(
        active.id, content, idempotencyKey,
        isSamePendingRequest || isStoredRetry ? undefined : requestSelection,
      );
      if (pendingMessageRequestRef.current?.idempotencyKey === idempotencyKey) {
        pendingMessageRequestRef.current = null;
      }
      setRetryableMessage(null);
      try {
        const key = pendingMessageStorageKey(active.id);
        const stored = sessionStorage.getItem(key);
        if (stored && (JSON.parse(stored) as { idempotencyKey?: string }).idempotencyKey === idempotencyKey) {
          sessionStorage.removeItem(key);
        }
      } catch { /* A stale key is harmless; server idempotency remains authoritative. */ }
      setAgentActivity("已整理当前计划，正在准备可供你检查的下一步…");
      setActive(updated); setMessage("");
      setSchedules((current) => {
        const next = { ...current };
        for (const candidate of updated.candidates) {
          if (!next[candidate.id] && (candidate.explicit_day || candidate.explicit_start_time)) {
            next[candidate.id] = { day: candidate.explicit_day ?? "", start_time: candidate.explicit_start_time ?? "" };
          }
        }
        return next;
      });
      const assistantPayload = updated.messages.at(-1)?.payload;
      const candidateIds = Array.isArray(assistantPayload?.candidate_ids)
        ? assistantPayload.candidate_ids.filter((id): id is string => typeof id === "string")
        : [];
      const scheduleSuggestions = Array.isArray(assistantPayload?.schedule_suggestions)
        ? assistantPayload.schedule_suggestions.filter((item): item is { candidate_id: string; day: string; start_time: string } => Boolean(item && typeof item === "object" && typeof (item as Record<string, unknown>).candidate_id === "string" && typeof (item as Record<string, unknown>).day === "string" && typeof (item as Record<string, unknown>).start_time === "string"))
        : [];
      if (assistantPayload?.action === "SCHEDULE_PROPOSAL" && scheduleSuggestions.length) {
        const scheduledIds = scheduleSuggestions.map((item) => item.candidate_id);
        const retainedLodgingIds = selected.filter((id) => updated.candidates.some((candidate) =>
          candidate.id === id && candidate.planning_category === "LODGING",
        ));
        const proposedIds = [...scheduledIds, ...retainedLodgingIds];
        const proposedSchedules = Object.fromEntries(scheduleSuggestions.map((item) => [item.candidate_id, { day: item.day, start_time: item.start_time }]));
        setSelected(proposedIds); setSchedules(proposedSchedules);
        setSessions((current) => [updated, ...current.filter((item) => item.id !== updated.id)]);
        setAgentActivity("正在核验地点之间的路线，并检查日期、节奏与时间冲突…");
        const draftKey = draftRequestKey(updated.id, proposedIds, scheduleSuggestions, false);
        try {
          const proposedDraft = await createAdvisorDraft(updated.id, proposedIds, scheduleSuggestions, false, draftKey);
          finishDraftRequest(draftKey);
          setActive(proposedDraft); setSelected(proposedDraft.draft?.selected_candidate_ids ?? proposedIds);
          setMapDay(proposedDraft.draft?.stops[0]?.day ?? "");
          setSessions((current) => [proposedDraft, ...current.filter((item) => item.id !== proposedDraft.id)]);
          setNotice("时间建议已准备好。检查地点、交通和空档后，确认即可更新正式行程。");
          setAgentActivity("");
        } catch (reason) {
          if (!handleDraftMemoryConflict(reason)) {
            setError(`已读取当前计划，但建议草案生成失败：${(reason as Error).message}`);
          }
        }
      } else {
        if (updated.trip_id) {
          setSelected(updated.context.selected_candidate_ids ?? []);
        } else if (candidateIds.length) {
          setSelected((current) => [...new Set([...current, ...candidateIds])]);
        } else if (selectionOverride) {
          setSelected(updated.context.selected_candidate_ids ?? selectionOverride);
        }
        setSessions((current) => [updated, ...current.filter((item) => item.id !== updated.id)]);
      }
      if (explicitCandidateIds.length) {
        setNotice(`${newlySelectedCandidates.map((candidate) => candidate.name).join("、")}已加入本次选择。补齐日期和时间后可更新预览；正式行程仍需你确认。`);
      }
      return true;
    } catch (reason) {
      if (explicitCandidateIds.length) setSelected(selected);
      const retryable = !(reason instanceof ApiError) || reason.status >= 500 || reason.status === 408 || reason.status === 429;
      setRetryableMessage(retryable ? { sessionId: active.id, content } : null);
      setError((reason as Error).message);
      return false;
    }
    finally {
      progressAbort.abort();
      if (progressAbortRef.current === progressAbort) progressAbortRef.current = null;
      setBusy(false); setAgentActivity("");
    }
  }

  async function switchPendingTask() {
    const nextTask = message.trim();
    if (!active || !nextTask || busy) return;
    const cancelled = await submitMessage(undefined, "取消");
    if (!cancelled) return;
    setMessage(nextTask);
    await submitMessage(undefined, nextTask);
  }

  async function generateDraft(allowProfileMemoryConflicts = false, automatic = false) {
    if (!active || !selected.length) return;
    if (!schedulesComplete) { setError("请为每个选中的地点确认日期和开始时间；只有对话中明确提到的时间才会预填。"); return; }
    setBusy(true); setError(""); setNotice(""); setMemoryConflictMessage("");
    const draftSchedules = scheduleRequiredIds.map((candidate_id) => ({ candidate_id, ...schedules[candidate_id] }));
    const draftKey = draftRequestKey(active.id, selected, draftSchedules, allowProfileMemoryConflicts);
    try {
      const updated = await createAdvisorDraft(active.id, selected, draftSchedules, allowProfileMemoryConflicts, draftKey);
      finishDraftRequest(draftKey);
      setActive(updated); setNotice(automatic
        ? "行程预览已根据你的选择自动更新；确认后才会写入正式行程。"
        : `第 ${updated.draft_version} 版行程调整预览已就绪；确认后会直接更新正式行程。`);
      setSelected(updated.draft?.selected_candidate_ids ?? selected);
      setMapDay(updated.draft?.stops[0]?.day ?? "");
      setSessions((current) => [updated, ...current.filter((item) => item.id !== updated.id)]);
      if (!automatic) window.requestAnimationFrame(() => {
        document.getElementById("advisor-itinerary-preview")?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    } catch (reason) {
      if (!handleDraftMemoryConflict(reason)) setError((reason as Error).message);
    }
    finally { setBusy(false); if (automatic) setAutoPreviewPending(false); }
  }

  async function saveAttentionDecision(itemKey: string, decision: "LATER" | "NOT_NEEDED" | "RESET"): Promise<boolean> {
    if (!active?.draft) return false;
    setBusy(true); setError("");
    try {
      const updated = await updateAdvisorChecklistDecision(active.id, itemKey, decision, active.draft.version);
      setActive(updated);
      setSessions((current) => [updated, ...current.filter((item) => item.id !== updated.id)]);
      setNotice(decision === "NOT_NEEDED" ? "已记录：这项对本次旅行不需要。" : decision === "LATER" ? "已暂缓这项检查；生成新草案后会再次提醒。" : "已恢复这项行程检查。");
      return true;
    } catch (reason) { setError((reason as Error).message); return false; }
    finally { setBusy(false); }
  }

  async function recommendDestinations() {
    if (!active) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const fingerprint = await advisorMessageFingerprint(JSON.stringify({
        sessionId: active.id,
        preference: active.preference_text,
        explorationCity: explorationCity.trim(),
        contextRevision: active.context?.revision ?? 0,
      }));
      const storageKey = `travelpilot-advisor-destination-pending:${active.id}`;
      let stored: { idempotencyKey?: string; fingerprint?: string } | null = null;
      try {
        const raw = sessionStorage.getItem(storageKey);
        stored = raw ? JSON.parse(raw) as { idempotencyKey?: string; fingerprint?: string } : null;
      } catch { stored = null; }
      const pending = pendingDestinationRequestRef.current;
      const idempotencyKey = pending?.sessionId === active.id && pending.fingerprint === fingerprint
        ? pending.idempotencyKey
        : fingerprint && stored?.fingerprint === fingerprint && stored.idempotencyKey
          ? stored.idempotencyKey
          : crypto.randomUUID();
      pendingDestinationRequestRef.current = {
        sessionId: active.id, fingerprint: fingerprint ?? "", idempotencyKey,
      };
      if (fingerprint) {
        try { sessionStorage.setItem(storageKey, JSON.stringify({ idempotencyKey, fingerprint })); }
        catch { /* The in-memory retry key still protects this page session. */ }
      }
      const updated = await recommendAdvisorDestinations(
        active.id, idempotencyKey, explorationCity.trim() || undefined,
      );
      pendingDestinationRequestRef.current = null;
      try { sessionStorage.removeItem(storageKey); } catch { /* Storage may be unavailable. */ }
      setActive(updated);
      setSessions((current) => [updated, ...current.filter((item) => item.id !== updated.id)]);
      setNotice(`${explorationCity.trim() ? `已按地图证据探索${explorationCity.trim()}` : "已按地图检索证据整理目的地候选"}；地点与出行条件仍需你确认。`);
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }

  async function chooseDestination(city: string) {
    if (!active) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const updated = await updateAdvisorSessionContext(active.id, {
        destination: city, start_date: startDate || null, end_date: endDate || null,
        budget: budget ? Number(budget) : null, pace_level: pace, preference_text: preference.trim(),
        origin: origin.trim(), companions: splitTags(companionsText), hard_constraints: splitTags(constraintsText),
      });
      setDestination(city); setActive(updated);
      setSelected(updated.context?.selected_candidate_ids ?? []);
      setSessions((current) => [updated, ...current.filter((item) => item.id !== updated.id)]);
      await submitMessage(undefined, `我选了${city}，请继续帮我规划。`);
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }

  async function confirmDraft(session: AdvisorSession | null = active) {
    if (!session?.draft) return;
    setBusy(true); setError("");
    try {
      const result = await confirmAdvisorDraft(session.id, session.draft.version);
      const confirmedAt = new Date().toISOString();
      setActive((current) => current?.id === session.id ? {
        ...current,
        status: "CONFIRMED",
        trip_id: result.trip_id,
        confirmed_trip_id: result.trip_id,
        confirmed_plan_id: result.plan_id,
        updated_at: confirmedAt,
      } : current);
      setSessions((current) => current.map((item) => item.id === session.id ? {
        ...item,
        status: "CONFIRMED",
        trip_id: result.trip_id,
        confirmed_trip_id: result.trip_id,
        confirmed_plan_id: result.plan_id,
        updated_at: confirmedAt,
      } : session));
      router.push(`/trips/${result.trip_id}/plan`);
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }

  async function confirmTaskWrite(task: AdvisorTaskSnapshot) {
    if (!active) return;
    if (active.draft) {
      if (draftIsStale) {
        setError("当前预览已过期，请先更新预览，再确认写入；不会重复向你询问同一问题。");
        return;
      }
      await confirmDraft(active);
      return;
    }
    if (!selected.length) {
      setError("还没有选中要写入的地点。请先在右侧选择地点，再确认日期和时间。");
      return;
    }
    if (!schedulesComplete) {
      setError("当前选择还缺少日期或开始时间。请先为每个地点补齐安排，顾问不会用重复提问代替写入。");
      return;
    }

    const question = `${task.goal} ${task.question ?? ""}`;
    const mealScope = /午餐/.test(question) ? "LUNCH" : /晚餐/.test(question) ? "DINNER" : null;
    const scopedSelection = mealScope
      ? selected.filter((id) => active.candidates.some((candidate) =>
        candidate.id === id && candidate.planning_category === "FOOD"
        && (!candidate.meal_type || candidate.meal_type === mealScope),
      ))
      : selected;
    if (!scopedSelection.length) {
      setError(mealScope
        ? `还没有选中${mealScope === "LUNCH" ? "午餐" : "晚餐"}地点。先选餐饮候选，再确认写入。`
        : "当前没有选中与这项确认对应的地点，请检查右侧选择后再试。");
      return;
    }
    const requestedDays = [...new Set(question.match(/20\d{2}-\d{2}-\d{2}/g) ?? [])];
    if (mealScope && requestedDays.length > 1) {
      const assignedDays = new Set(scopedSelection.map((id) => schedules[id]?.day).filter(Boolean));
      const missingDays = requestedDays.filter((day) => !assignedDays.has(day));
      if (missingDays.length) {
        setError(`这项确认涉及 ${requestedDays.length} 天，但选中的餐饮地点没有覆盖 ${missingDays.join("、")}。请给每一天各选地点并设置时间后再确认。`);
        return;
      }
    }

    // The task scope is only for validating that the requested meal was
    // selected. Draft confirmation replaces the whole plan, so generate it
    // from every current selection; otherwise confirming one meal silently
    // drops the other meals, attractions, and lodging from the plan snapshot.
    const draftSchedules = selected
      .filter((candidateId) => active.candidates.some((candidate) =>
        candidate.id === candidateId && candidate.planning_category !== "LODGING",
      ))
      .map((candidate_id) => ({ candidate_id, ...schedules[candidate_id] }));
    const draftKey = draftRequestKey(active.id, selected, draftSchedules, false);
    setBusy(true); setError(""); setNotice("");
    try {
      const updated = await createAdvisorDraft(active.id, selected, draftSchedules, false, draftKey);
      finishDraftRequest(draftKey);
      setActive(updated);
      setSelected(updated.draft?.selected_candidate_ids ?? selected);
      setMapDay(updated.draft?.stops[0]?.day ?? "");
      setSessions((current) => [updated, ...current.filter((item) => item.id !== updated.id)]);
      if (!updated.draft) throw new Error("服务端没有返回可提交的行程草案，请先检查预览后重试。");
      const result = await confirmAdvisorDraft(updated.id, updated.draft.version);
      const confirmedAt = new Date().toISOString();
      setActive((current) => current?.id === updated.id ? {
        ...current, status: "CONFIRMED", trip_id: result.trip_id,
        confirmed_trip_id: result.trip_id, confirmed_plan_id: result.plan_id,
        updated_at: confirmedAt,
      } : current);
      setSessions((current) => current.map((item) => item.id === updated.id ? {
        ...item, status: "CONFIRMED", trip_id: result.trip_id,
        confirmed_trip_id: result.trip_id, confirmed_plan_id: result.plan_id,
        updated_at: confirmedAt,
      } : item));
      router.push(`/trips/${result.trip_id}/plan`);
    } catch (reason) {
      finishDraftRequest(draftKey);
      if (!handleDraftMemoryConflict(reason)) setError((reason as Error).message);
    } finally { setBusy(false); }
  }

  function toggleCandidate(id: string) {
    setSelected((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
    setAutoPreviewRevision((revision) => revision + 1);
  }

  function updateCandidateSchedule(id: string, field: "day" | "start_time", value: string) {
    setSchedules((current) => ({
      ...current,
      [id]: { ...(current[id] ?? { day: "", start_time: "" }), [field]: value },
    }));
    setAutoPreviewRevision((revision) => revision + 1);
  }

  const canCreateSession = setupChoice === "ideas" || (setupChoice === "trip" && Boolean(tripId));
  const scheduleRequiredIds = active?.candidates
    .filter((candidate) => selected.includes(candidate.id) && candidate.planning_category !== "LODGING")
    .map((candidate) => candidate.id) ?? [];
  const schedulesComplete = selected.length > 0 && scheduleRequiredIds.every((id) => Boolean(schedules[id]?.day && schedules[id]?.start_time));
  const selectedScheduleFingerprint = JSON.stringify({
    session_id: active?.id ?? "",
    selected,
    schedules: scheduleRequiredIds.map((candidate_id) => ({ candidate_id, ...schedules[candidate_id] })),
  });
  useEffect(() => {
    if (autoPreviewRevision === 0) return;
    if (busy) return;
    const hasMissingPlanningContext = Boolean(active?.context?.planning_state?.missing_required?.length);
    if (!active || active.status !== "ACTIVE" || !selected.length || !schedulesComplete
      || hasMissingPlanningContext || (active.draft && !draftIsStale)) {
      setAutoPreviewPending(false);
      return;
    }
    if (lastAutoPreviewFingerprint.current === selectedScheduleFingerprint) return;
    const timer = window.setTimeout(() => {
      lastAutoPreviewFingerprint.current = selectedScheduleFingerprint;
      setAutoPreviewPending(true);
      void generateDraft(false, true);
    }, 600);
    return () => window.clearTimeout(timer);
  }, [active, autoPreviewRevision, busy, draftIsStale, schedulesComplete, selected, selectedScheduleFingerprint]);
  const travelDays = (() => {
    if (!active?.start_date || !active.end_date) return [];
    const start = new Date(`${active.start_date}T00:00:00Z`);
    const count = Math.min(31, Math.max(0, Math.round((Date.parse(`${active.end_date}T00:00:00Z`) - start.getTime()) / 86400000) + 1));
    return Array.from({ length: count }, (_, index) => {
      const day = new Date(start); day.setUTCDate(day.getUTCDate() + index); return day.toISOString().slice(0, 10);
    });
  })();
  const draftDays = [...new Set(active?.draft?.stops.map((stop) => stop.day) ?? [])];
  const visibleMapDay = draftDays.includes(mapDay) ? mapDay : draftDays[0] ?? "";
  const mapStops = active?.draft?.stops.filter((stop) => stop.day === visibleMapDay) ?? [];
  const allAttentionItems = active?.draft?.attention_items ?? [];
  const selectedUnscheduledMealCandidates = active?.candidates.filter((candidate) =>
    candidate.planning_category === "FOOD" && selected.includes(candidate.id)
    && (!schedules[candidate.id]?.day || !schedules[candidate.id]?.start_time),
  ) ?? [];
  const locallyCoveredAttentionItems = allAttentionItems.filter((item) => {
    if (item.kind === "LODGING_MISSING") {
      return active?.candidates.some((candidate) =>
        candidate.planning_category === "LODGING" && selected.includes(candidate.id),
      ) ?? false;
    }
    if (item.kind !== "MEAL_MISSING") return false;
    return mealCoveredInCurrentWork(item.day, item.meal_type === "LUNCH" ? "午餐" : "晚餐");
  });
  const locallyCoveredAttentionKeys = new Set(locallyCoveredAttentionItems.map(checklistItemKey));
  const currentAttentionItems = allAttentionItems.filter((item) => !locallyCoveredAttentionKeys.has(checklistItemKey(item)));
  const handledAttentionItems = currentAttentionItems.filter((item) => {
    const decision = active?.context?.checklist_decisions?.[checklistItemKey(item)];
    return decision?.decision === "NOT_NEEDED" || (decision?.decision === "LATER" && decision.draft_version === active?.draft?.version);
  });
  const visibleAttentionItems = currentAttentionItems.filter((item) => {
    const decision = active?.context?.checklist_decisions?.[checklistItemKey(item)];
    const isHandled = decision?.decision === "NOT_NEEDED" || (decision?.decision === "LATER" && decision.draft_version === active?.draft?.version);
    return showHandledAttention ? isHandled : !isHandled;
  });
  const selectedAttentionKey = active && selectedAttention?.sessionId === active.id ? selectedAttention.itemKey : null;
  const selectedAttentionItem = visibleAttentionItems.find((item) => checklistItemKey(item) === selectedAttentionKey) ?? null;
  const sessionList = sessions.length ? <div className="advisor-live-session-list">{sessions.map((item) => <article key={item.id} className={`advisor-session-row${active?.id === item.id ? " is-active" : ""}`}>
    <button type="button" className="advisor-session-open" onClick={() => openSession(item)}>
      <span><strong>{item.destination || "旅行想法"}</strong><small>{item.updated_at.slice(0, 16).replace("T", " ")} · {item.status === "CONFIRMED" ? "已确认" : item.draft ? "草案待确认" : "对话中"}</small></span><span>打开 →</span>
    </button>
    <button type="button" className="advisor-session-delete" aria-label={`删除${item.destination || "旅行想法"}对话`} title="删除对话" disabled={deletingSessionId === item.id} onClick={() => void deleteSession(item)}>{deletingSessionId === item.id ? "删除中…" : "删除"}</button>
  </article>)}</div> : <p className="advisor-empty">还没有顾问会话。开始一段新对话后，它会保存在这里。</p>;

  function startNewConversation() {
    setContextEditorOpen(false);
    setTripActionsExpanded(false);
    setActive(null); setMode(selectedTripId ? "existing" : "discover"); setSetupChoice(selectedTripId ? "trip" : "ideas"); setTripId(selectedTripId);
    setDestination(""); setStartDate(""); setEndDate(""); setBudget(""); setPace(3);
    setPreference(""); setOrigin(""); setCompanionsText(""); setConstraintsText("");
    setSelected([]); setSchedules({}); setMessage(""); setError(""); setNotice(""); setHistoryOpen(false); setStage("setup");
    router.replace("/advisor");
  }

  function focusPlanningSetup(nextMode: Mode, nextPreference?: string) {
    setActive(null); setStage("setup"); setHistoryOpen(false);
    setSetupChoice(nextMode === "existing" ? "trip" : "ideas");
    setMode(nextMode === "existing" ? "existing" : "discover");
    if (nextMode === "existing" && !tripId) setTripId(selectedTripId || trips[0]?.id || "");
    if (nextPreference) setPreference(nextPreference);
  }

  function openSession(item: AdvisorSession) {
    setContextEditorOpen(false);
    setTripActionsExpanded(false);
    setCandidateFilter("AUTO"); setCandidateMapOpen(false);
    setActive(item); setSelected(item.context?.selected_candidate_ids ?? item.draft?.selected_candidate_ids ?? []);
    setSchedules(schedulesForSession(item));
    setTripId(item.trip_id || "");
    setDestination(item.destination || ""); setStartDate(item.start_date || ""); setEndDate(item.end_date || "");
    setBudget(item.budget == null ? "" : String(item.budget)); setPace(item.pace_level); setPreference(item.preference_text || "");
    setOrigin(item.context?.planning_inputs?.origin ?? "");
    setCompanionsText((item.context?.planning_inputs?.companions ?? []).join("、"));
    setConstraintsText((item.context?.planning_inputs?.hard_constraints ?? []).join("、"));
    setError(""); setNotice(""); setHistoryOpen(false); setStage("chat");
    router.replace(`/advisor?sessionId=${item.id}`);
  }

  async function deleteSession(item: AdvisorSession) {
    const label = item.destination || "这段旅行想法";
    if (!window.confirm(`删除「${label}」这段顾问对话？\n\n对话内容和未确认草案会被删除且无法恢复。已保存的旅行和正式行程计划不会删除。`)) return;
    setDeletingSessionId(item.id);
    setError("");
    try {
      await removeAdvisorSession(item.id);
      setSessions((current) => current.filter((session) => session.id !== item.id));
      if (active?.id === item.id) {
        setActive(null);
        setSelected([]);
        setSchedules({});
        setMessage("");
        setStage("home");
        setHistoryOpen(false);
        router.replace("/advisor");
      }
      setNotice(`已删除「${label}」的顾问对话。关联的旅行和正式行程仍保留。`);
    } catch (reason) {
      setError(`删除对话失败：${(reason as Error).message}`);
    } finally {
      setDeletingSessionId(null);
    }
  }

  async function refreshAfterGuideImport() {
    if (!active) return;
    try {
      const updated = await getAdvisorSession(active.id);
      setActive(updated);
      setSchedules(schedulesForSession(updated));
      setSessions((current) => [updated, ...current.filter((session) => session.id !== updated.id)]);
      setNotice("攻略地点已导入并重新载入。请从候选中选择要加入行程的地点。");
    } catch (reason) {
      setError(`攻略已导入，但刷新地点候选失败：${(reason as Error).message}`);
    }
  }

  async function refreshAfterWhatIfApplied() {
    if (!active) return;
    try {
      const updated = await getAdvisorSession(active.id);
      setActive(updated);
      setSelected(updated.context?.selected_candidate_ids ?? []);
      setSchedules(schedulesForSession(updated));
      setSessions((current) => [updated, ...current.filter((session) => session.id !== updated.id)]);
      setNotice("途中调整已写入正式行程，并已刷新顾问上下文。");
    } catch (reason) {
      setNotice("途中调整已写入正式行程；顾问上下文刷新失败，请重新载入对话后继续。");
      setError((reason as Error).message);
    }
  }

  function chooseSetup(choice: "trip" | "ideas") {
    setSetupChoice(choice);
    if (choice === "trip") {
      setMode("existing");
      if (!tripId) setTripId(selectedTripId || trips[0]?.id || "");
    } else {
      setMode("discover"); setTripId(""); setDestination(""); setStartDate(""); setEndDate(""); setBudget("");
    }
  }

  const latestAssistantMessageId = active?.messages.slice().reverse()
    .find((item) => item.role === "assistant")?.id;

  return (
    <main className="shell advisor-page advisor-live">
      <header className="advisor-heading">
        <div><h1>和旅行顾问一起，<br className="advisor-title-break" />把想法变成行程。</h1><p>先选择一段旅行，或从一个想法开始。对话和草案会自动保存，正式行程只在你确认后写入。</p></div>
        {stage === "chat" && <div className="advisor-chat-toolbar"><button type="button" className="secondary" onClick={startNewConversation}>＋ 新建对话</button><button type="button" className="secondary" onClick={() => setHistoryOpen((value) => !value)}>{historyOpen ? "收起历史" : "找之前的对话"}</button></div>}
      </header>
      {error && stage !== "setup" && <div className="advisor-live-error" role="alert"><span>{error}</span>{retryableMessage?.sessionId === active?.id && <button type="button" className="advisor-retry-action" disabled={busy} onClick={() => { const request = retryableMessage; if (request) void submitMessage(undefined, request.content); }}>重试刚才请求</button>}</div>}
      {memoryConflictMessage && stage === "chat" && <section className="advisor-memory-conflict" role="alert">
        <strong>地点与你的长期记忆不一致</strong>
        <p>{memoryConflictMessage}</p>
        <small>长期记忆是你的偏好参考。本次确认只对这份草案有效，不会修改或删除记忆，也不会保存正式行程。</small>
        <div><button type="button" disabled={busy} onClick={() => void generateDraft(true)}>我确认，本次仍按所选地点继续</button>
          <button type="button" className="secondary" disabled={busy} onClick={() => setMemoryConflictMessage("")}>返回调整地点</button></div>
      </section>}
      {notice && stage !== "setup" && <p role="status" className="advisor-action-notice">{notice}</p>}

      {stage === "chat" && <nav className="advisor-workflow" aria-label="规划进度">
        {["聊想法", "选地点", "安排行程"].map((step, index) => {
          const number = index + 1;
          return <div className={`advisor-workflow-step${advisorStep === number ? " is-current" : ""}${advisorStep > number ? " is-done" : ""}`} key={step} aria-current={advisorStep === number ? "step" : undefined}>
            <span>{advisorStep > number ? "✓" : number}</span><strong>{step}</strong>
          </div>;
        })}
      </nav>}

      {stage === "home" && <section className="advisor-launch panel">
        <div className="advisor-launch-copy"><span className="advisor-welcome-mark" aria-hidden="true">✳</span><div><p className="eyebrow">你的旅行，从这里开始</p><h2>今天想聊哪一段旅程？</h2><p>可以围绕已创建的旅行继续规划，也可以先聊聊想去哪里、喜欢什么。</p></div></div>
        <div className="advisor-launch-actions"><button type="button" className="primary" onClick={startNewConversation}>开始新对话 <span>↗</span></button><button type="button" className="secondary" onClick={() => document.getElementById("advisor-history")?.scrollIntoView({ behavior: "smooth", block: "start" })}>找之前的对话 ↓</button></div>
        <section className="advisor-home-history" id="advisor-history"><div className="advisor-panel-title"><div><h2>之前的对话</h2><p>打开后可以继续补充条件、挑选地点或确认草案。</p></div><span className="social-count">{sessions.length}</span></div>{sessionList}</section>
      </section>}

      {stage === "setup" && <section className="advisor-start panel" id="advisor-start">
        <div className="advisor-panel-title"><div><p className="eyebrow">新对话 · 第 1 步</p><h2>先选择这段旅行</h2><p>选择已有旅行会自动读取目的地、日期和计划状态；没有旅行也可以从想法开始，之后再创建行程。</p></div><button type="button" className="secondary" onClick={() => setStage("home")}>返回</button></div>
        <div className="advisor-entry-choice" role="group" aria-label="选择旅行来源">
          <button type="button" className={setupChoice === "trip" ? "is-selected" : ""} aria-pressed={setupChoice === "trip"} onClick={() => chooseSetup("trip")}><span>🧭</span><strong>关联已有旅行</strong><small>无论是否已有行程计划，都可以接着咨询</small></button>
          <button type="button" className={setupChoice === "ideas" ? "is-selected" : ""} aria-pressed={setupChoice === "ideas"} onClick={() => chooseSetup("ideas")}><span>✦</span><strong>我还没有旅行</strong><small>先聊聊旅行想法，地点和日期之后再确定</small></button>
        </div>
        {runtimeStatus && <p className={`advisor-provider-status${runtimeStatus.llm_status !== "CONFIGURED" ? " is-local" : ""}`} role="status">
          {runtimeStatus.llm_status === "CONFIGURED"
            ? `模型已配置：${runtimeStatus.model_name} · 单轮输出上限 ${runtimeStatus.output_token_limit} tokens。上游连通状态会在实际请求后显示。`
            : runtimeStatus.llm_status === "NOT_CONFIGURED"
              ? "模型未配置 · 将使用本地规则识别与模板回复。"
              : "暂时无法读取模型配置状态；请检查 API 服务，并确认它已更新至当前版本。"}
        </p>}
        <form className="advisor-live-form" onSubmit={startSession}>
          {setupChoice === "trip" ? <label className="advisor-setup-trip">选择要咨询的旅行<select value={tripId} onChange={(event) => setTripId(event.target.value)} required>
            <option value="">选择一段旅行</option>{trips.map((trip) => <option key={trip.id} value={trip.id}>{tripOptionLabel(trip, trips)} · {trip.has_plan ? "已有行程" : "尚无行程"}</option>)}
          </select>{selectedTrip && <small className="advisor-trip-status-note"><strong>{selectedTrip.has_plan ? "已有行程计划" : "这段旅行还没有行程计划"}</strong> · {selectedTrip.destination} · {selectedTrip.start_date} 至 {selectedTrip.end_date} · 预算 {selectedTrip.budget == null ? "未设置" : `${selectedTrip.budget} 元`} · 节奏 {selectedTrip.pace_level}/5。{selectedTrip.has_plan ? "顾问会读取现有计划，供你调整。" : "顾问可以从空白计划开始安排。"}</small>}</label> : setupChoice === "ideas" ? <div className="advisor-ideas-note"><strong>从想法开始，不必先创建旅行</strong><p>创建对话后，可以直接告诉顾问“想去海边”“想吃当地小吃”或任何还不成熟的念头。确认行程草案时再创建正式旅行。</p></div> : <div className="advisor-ideas-note"><strong>请选择一种开始方式</strong><p>关联已有旅行，或选择还没有旅行、先聊旅行想法。</p></div>}
          {(setupChoice === "trip" || setupChoice === "ideas") && <><label>旅行节奏<select value={pace} onChange={(event) => setPace(Number(event.target.value))}><option value={1}>很轻松</option><option value={2}>轻松</option><option value={3}>适中</option><option value={4}>紧凑</option><option value={5}>很紧凑</option></select></label>
          <label className="advisor-live-preference">先告诉顾问一点偏好（可选）<input value={preference} onChange={(event) => setPreference(event.target.value)} placeholder="比如：喜欢美食、慢节奏，或带老人同行" /></label></>}
          <button type="submit" className="primary" disabled={!canCreateSession || busy || (setupChoice === "trip" && trips.length === 0)}>{busy ? "处理中…" : setupChoice === "trip" ? "进入这段旅行的对话" : "开始聊旅行想法"}</button>
        </form>
        {setupChoice === "trip" && trips.length === 0 && <p className="advisor-empty">你还没有创建旅行。可以先切换到“我还没有旅行”开始聊天，也可以<Link href="/trips/new">先创建一段旅行</Link>。</p>}
      </section>}

      {stage === "chat" && <>{historyOpen && <section className="advisor-live-history panel"><div className="advisor-panel-title"><div><h2>切换到之前的对话</h2><p>选择一段已保存的顾问会话继续。</p></div></div>{sessionList}</section>}<div
        className={`advisor-live-columns${chatExpanded ? " is-chat-expanded" : ""}`}
        ref={advisorColumnsRef}
        style={{ gridTemplateColumns: chatExpanded ? "minmax(0, 1fr)" : `minmax(360px, ${chatRatio}fr) 14px minmax(420px, ${100 - chatRatio}fr)` }}
      >
        <section className="advisor-chat panel" aria-label="顾问对话" ref={advisorChatPanelRef}>
          <div className="advisor-panel-title"><div><h2>对话</h2><p>{active ? `${active.destination || selectedTrip?.destination || "未设置目的地"} · ${active.status === "ACTIVE" ? "等待你的行程确认" : "已确认"}` : "创建或打开一个会话后开始"}</p></div>
            <div className="advisor-panel-title-actions">
              <button type="button" className="advisor-expand-chat" aria-pressed={chatExpanded} onClick={() => setChatExpanded((expanded) => !expanded)}>{chatExpanded ? "返回左右布局" : "放大对话"}</button>
              {active && <span className="advisor-demo-mark">已保存</span>}
            </div></div>
          {active ? <>
            {active.status === "ACTIVE" && <details className="advisor-context-editor" open={contextEditorOpen || Boolean(active.context?.planning_state?.missing_required?.length)}>
              <summary>{active.context?.planning_state?.missing_required?.length
                ? `旅行条件待补充：${active.context.planning_state.missing_required.map((slot) => ({ destination: "目的地", start_date: "开始日期", end_date: "结束日期" } as Record<string, string>)[slot] ?? slot).join("、")}`
                : "旅行条件 · 可随时调整"}</summary>
              <form className="advisor-context-form" onSubmit={saveSessionContext}>
                {active.mode === "existing" && <p className="advisor-context-note">顾问会先展示行程调整预览；你确认后，才会更新正式计划。</p>}
                <label>目的地<input value={destination} onChange={(event) => setDestination(event.target.value)} placeholder="城市或区域" /></label>
                <label>开始日期<input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label>
                <label>结束日期<input type="date" value={endDate} min={startDate || undefined} onChange={(event) => setEndDate(event.target.value)} /></label>
                <label>预算<input type="number" min="0" value={budget} onChange={(event) => setBudget(event.target.value)} placeholder="可选，元" /></label>
                <label>节奏<select value={pace} onChange={(event) => setPace(Number(event.target.value))}><option value={1}>很轻松</option><option value={2}>轻松</option><option value={3}>适中</option><option value={4}>紧凑</option><option value={5}>很紧凑</option></select></label>
                <label className="advisor-context-preference">偏好与限制<input value={preference} onChange={(event) => setPreference(event.target.value)} placeholder="美食、博物馆、饮食限制等" /></label>
                <label>出发地<input id="advisor-origin-input" value={origin} onChange={(event) => setOrigin(event.target.value)} placeholder="例如：成都东站" /></label>
                <label>同行人<input value={companionsText} onChange={(event) => setCompanionsText(event.target.value)} placeholder="儿童、长辈，用顿号分隔" /></label>
                <label>硬性限制<input value={constraintsText} onChange={(event) => setConstraintsText(event.target.value)} placeholder="例如：不去博物馆、花生过敏" /></label>
                <small className="advisor-context-note">出发地经地图精确核验后，会参与首日首个地点的交通时间估算。</small>
                <button type="submit" className="secondary" disabled={busy}>{busy ? "保存中…" : "保存旅行条件"}</button>
              </form>
              {active.mode === "discover" && !active.destination && <label className="advisor-context-preference">想探索哪座城市？（可选）<input value={explorationCity} onChange={(event) => setExplorationCity(event.target.value)} maxLength={80} placeholder="例如：泉州；留空会给你城市灵感" /></label>}
              {active.mode === "discover" && !active.destination && <button type="button" className="secondary advisor-recommend-button" onClick={recommendDestinations} disabled={busy || active.status !== "ACTIVE"}>
                {busy ? "正在查询地图…" : preference.trim() ? "按这些想法找目的地" : "先给我一些目的地灵感"}
              </button>}
              {active.mode === "discover" && !active.destination && <small className="advisor-context-note">可以指定任意城市；留空时先探索成都、杭州、西安、厦门、青岛、长沙。候选来自高德地点检索，不代表全网热度，也不含价格或城市间交通时长。每次最多执行 12 次地图搜索。</small>}
            </details>}
            {active.trip_id && <div className="advisor-context-card">
              <strong>当前计划与已确认记忆</strong>
              {active.context?.current_plan ? <p>计划 v{active.context.current_plan.version} · {active.context.current_plan.stops.length} 个地点 · {active.context.current_plan.warnings.length} 条原计划提示</p> : <p>这趟旅行还没有已保存计划。</p>}
              {active.context?.confirmed_memories?.length ? <ul>{active.context.confirmed_memories.slice(0, 4).map((memory) => <li key={memory.id}>{memory.text}</li>)}</ul> : <small>没有已确认的旅行记忆</small>}
            </div>}
            {uniqueTripActionItems.length > 0 && active.status === "ACTIVE" && <section className="advisor-trip-action-panel" aria-label="这段旅行可以继续完成的事项">
              <div><strong>接下来可以一起补齐</strong><p>这些建议来自当前行程检查；是否处理、如何安排都由你决定。</p></div>
              <ul>{visibleTripActionItems.map((item) => <li key={item.key}>
                <span><strong>{item.title}</strong><small>{item.detail}</small></span>
                <button type="button" disabled={busy} onClick={() => {
                  if (item.action === "EDIT_ORIGIN") {
                    setContextEditorOpen(true);
                    window.requestAnimationFrame(() => {
                      const input = document.getElementById("advisor-origin-input") as HTMLInputElement | null;
                      input?.scrollIntoView({ behavior: "smooth", block: "center" });
                      input?.focus();
                    });
                    return;
                  }
                  void submitMessage(undefined, item.prompt);
                }}>{item.action_label}</button>
              </li>)}</ul>
              {uniqueTripActionItems.length > 5 && <button type="button" className="advisor-trip-action-more" onClick={() => setTripActionsExpanded((expanded) => !expanded)}>
                {tripActionsExpanded ? "收起事项" : `查看另外 ${uniqueTripActionItems.length - 5} 项`}
              </button>}
            </section>}
            <div className="advisor-messages" aria-live="polite" ref={messagesRef} style={{ height: `${chatMessagesHeight}px`, maxHeight: `${chatMessagesHeight}px`, flex: "none" }}>
              {active.messages.map((item) => {
                const trace = readAgentTrace(item.payload);
                const transitions = trace?.transitions ?? [];
                const evidenceSummary = item.role === "assistant" ? readAdvisorEvidenceSummary(item.payload) : null;
                const weatherForecast = item.role === "assistant" ? readAdvisorWeatherForecast(item.payload) : null;
                const scheduleBreaks = item.role === "assistant" && Array.isArray(item.payload?.schedule_breaks)
                  ? item.payload.schedule_breaks.filter((entry): entry is { day: string; label: string; start_time: string; end_time: string; time_is_proposed_default: boolean } => Boolean(
                    entry && typeof entry === "object"
                    && typeof (entry as Record<string, unknown>).day === "string"
                    && typeof (entry as Record<string, unknown>).label === "string"
                    && typeof (entry as Record<string, unknown>).start_time === "string"
                    && typeof (entry as Record<string, unknown>).end_time === "string",
                  )) : [];
                const routeUnavailable = ["UNAVAILABLE", "REJECTED"].includes(trace?.route_evidence ?? "");
                const task = item.role === "assistant" ? readAdvisorTask(item.payload) : null;
                const taskLabel = task ? advisorTaskLabel(task) : null;
                const confirmationOptions = task?.confirmation_options ?? [];
                const isLatestAssistantMessage = item.role === "assistant" && item.id === latestAssistantMessageId;
                const responseSource = typeof item.payload?.response_source === "string" ? item.payload.response_source : null;
                const responseModel = typeof item.payload?.llm_model === "string" ? item.payload.llm_model : null;
                const fallbackReason = typeof item.payload?.response_fallback_reason === "string" ? item.payload.response_fallback_reason : null;
                const rawMemorySuggestion = item.payload?.memory_suggestion as Partial<AdvisorMemorySuggestion> | null | undefined;
                const memorySuggestion = item.role === "assistant" && rawMemorySuggestion
                  && typeof rawMemorySuggestion.category === "string"
                  && typeof rawMemorySuggestion.text === "string"
                  && typeof rawMemorySuggestion.evidence === "string"
                  ? rawMemorySuggestion as AdvisorMemorySuggestion : null;
                const transportRoute = readAdvisorRoute(item.payload);
                const eventReplanHandoff = item.role === "assistant"
                  && item.payload?.event_replan_handoff
                  && typeof item.payload.event_replan_handoff === "object"
                  ? item.payload.event_replan_handoff as {
                    event_type?: string; action_label?: string;
                    prefill?: { condition?: string; delay_min?: number };
                  }
                  : null;
                const draftFollowUps = item.role === "assistant" ? readDraftFollowUpOptions(item.payload) : [];
                const budgetContinuation = item.role === "assistant" && item.payload?.budget_continuation && typeof item.payload.budget_continuation === "object"
                  ? item.payload.budget_continuation as { required?: boolean; action_label?: string; prompt?: string; grant_output_tokens?: number; used_output_tokens?: number; session_output_limit?: number }
                  : null;
                const storedFeedback = item.role === "assistant" && item.payload?.advisor_feedback && typeof item.payload.advisor_feedback === "object"
                  ? item.payload.advisor_feedback as { rating?: AdvisorMessageFeedbackRating; reason?: AdvisorMessageFeedbackReason | null }
                  : null;
                return <article className={`advisor-message ${item.role}`} key={item.id}>
                  <span>{item.role === "user" ? "你" : "顾问"}</span><p>{item.content}</p>
                  {weatherForecast && <section className={`advisor-message-weather${weatherForecast.source === "Local simulated forecast" ? " is-simulated" : ""}`} aria-label="本轮旅行天气证据">
                    <div className="advisor-message-weather-heading">
                      <strong>{weatherForecast.city ? `${weatherForecast.city} · ` : ""}天气参考</strong>
                      <span>{weatherForecast.source === "Local simulated forecast" ? "本地模拟 · 非真实预报" : weatherForecast.source}</span>
                    </div>
                    {weatherForecast.days.length ? <div className="advisor-message-weather-days">{weatherForecast.days.map((day) => <article key={day.date}>
                      <strong>{day.date}</strong><span>{day.description}</span>
                      <small>{day.temperature_min_c ?? "—"}°–{day.temperature_max_c ?? "—"}°</small>
                      {day.precipitation_probability_max != null && <small>降水概率 {day.precipitation_probability_max}%</small>}
                    </article>)}</div> : <p>{weatherForecast.message || "这段日期暂时没有可用预报。"}</p>}
                    <small>{weatherForecast.attribution || `更新时间：${weatherForecast.fetched_at.slice(0, 16).replace("T", " ")}`}</small>
                  </section>}
                  {scheduleBreaks.length > 0 && <section className="advisor-reserved-breaks" aria-label="草案中的用餐休息时间">
                    <strong>已在草案中预留用餐时间</strong>
                    <ul>{scheduleBreaks.map((item, index) => <li key={`${item.day}:${item.label}:${item.start_time}:${index}`}>
                      <span>{item.day} · {item.label} {item.start_time}–{item.end_time}</span>
                      <small>{item.time_is_proposed_default ? "建议时段，可调整；未安排餐厅" : "按你指定的时段预留"}</small>
                    </li>)}</ul>
                  </section>}
                  {eventReplanHandoff?.event_type && active?.trip_id && <div className="advisor-event-replan-handoff">
                    <small>下一步会先生成途中调整预览。请核对生效时间及相关信息；确认前不会写入新计划。</small>
                    <button className="advisor-event-replan-action" type="button" onClick={() => setWhatIf({ tripId: active.trip_id!, eventType: eventReplanHandoff.event_type!, prefill: eventReplanHandoff.prefill })}>{eventReplanHandoff.action_label || "核对信息并查看途中调整预览"} →</button>
                  </div>}
                  {item.role === "user" && /记住|以后.{0,8}(?:偏好|规划|安排)|(?:偏好|习惯).{0,8}(?:保存|记住)/.test(item.content) && <div className="advisor-save-memory-actions">
                    {savedMemoryMessageIds.includes(item.id) ? <span>已保存到偏好记忆</span> : item.content.trim().length > 1000 ? <span>内容较长，请到账户页填写偏好摘要</span> : <>
                      <small>保存时请选择它的作用；旅行顾问不会替你判断这是偏好还是必须遵守的限制。</small>
                      <button className="advisor-save-memory" type="button" disabled={savingMemoryMessageId !== null || item.content.trim().length < 2} onClick={() => void saveExplicitMemory(item.id, item.content, "PREFERENCE")}>
                        {savingMemoryMessageId === item.id ? "保存中…" : "保存为旅行偏好"}
                      </button>
                      <button className="advisor-save-memory is-limit" type="button" disabled={savingMemoryMessageId !== null || item.content.trim().length < 2} onClick={() => void saveExplicitMemory(item.id, item.content, "HARD_LIMIT")}>
                        {savingMemoryMessageId === item.id ? "保存中…" : "保存为必须遵守的限制"}
                      </button>
                    </>}
                  </div>}
                  {memorySuggestion && !dismissedMemorySuggestionIds.includes(item.id) && <div className="advisor-save-memory-actions" aria-label="顾问提出的偏好记忆建议">
                    {savedMemoryMessageIds.includes(item.id) ? <span>已按你的确认保存</span> : <>
                      <small>顾问认为这可能是适合长期使用的旅行偏好，仅供你审阅；不会自动保存，也不会自动视为硬性限制。</small>
                      <p><strong>{{ PACE: "旅行节奏", INTEREST: "兴趣偏好", FOOD: "餐饮偏好", LODGING: "住宿偏好", TRANSPORT: "交通偏好", GENERAL: "旅行偏好" }[memorySuggestion.category]}</strong> · {memorySuggestion.text}</p>
                      <small>依据你的原话：“{memorySuggestion.evidence}”</small>
                      <button className="advisor-save-memory" type="button" disabled={savingMemoryMessageId !== null} onClick={() => void saveSuggestedMemory(item.id, memorySuggestion, "PREFERENCE")}>
                        {savingMemoryMessageId === item.id ? "保存中…" : "确认保存为偏好"}
                      </button>
                      <button className="advisor-save-memory is-limit" type="button" disabled={savingMemoryMessageId !== null} onClick={() => void saveSuggestedMemory(item.id, memorySuggestion, "HARD_LIMIT")}>
                        确认为必须遵守的限制
                      </button>
                      <button className="advisor-save-memory" type="button" onClick={() => setDismissedMemorySuggestionIds((current) => [...current, item.id])}>忽略</button>
                    </>}
                  </div>}
                  {draftFollowUps.filter((option) => !dismissedDraftFollowUps.includes(`${item.id}:${option.key}`)).length > 0 && <section className="advisor-draft-followups" aria-label="草案后续建议">
                    <div><strong>这份草案还有待完善的时段</strong><p>可以先查询餐饮或活动候选，由你决定是否添加。选点后需要重新检查草案。</p></div>
                    {draftFollowUps.filter((option) => !dismissedDraftFollowUps.includes(`${item.id}:${option.key}`)).map((option) => <div className="advisor-draft-followup" key={option.key}>
                      <span><strong>{option.title}</strong><small>{option.detail}</small></span>
                      <button type="button" disabled={busy || active.status !== "ACTIVE"} onClick={() => submitMessage(undefined, option.prompt)}>{option.action_label}</button>
                      {active.trip_id && <button type="button" className="advisor-draft-followup-import" disabled={busy || active.status !== "ACTIVE"} onClick={() => setImportOpen(true)} title="从攻略提取地点；确认地点后才会加入旅行收藏">从攻略导入</button>}
                      <button type="button" className="advisor-draft-followup-later" onClick={() => setDismissedDraftFollowUps((current) => [...current, `${item.id}:${option.key}`])}>稍后</button>
                    </div>)}
                  </section>}
                  {budgetContinuation?.required && <section className="advisor-draft-followups" aria-label="模型额度确认">
                    <div><strong>本次对话的模型额度已用完</strong><p>已用 {budgetContinuation.used_output_tokens ?? 0} / {budgetContinuation.session_output_limit ?? 0} 个输出 token。继续会增加一轮上限（最多 {budgetContinuation.grant_output_tokens ?? 0} token），并恢复刚才的任务。</p></div>
                    <div className="advisor-draft-followup"><span><strong>是否继续？</strong><small>仅在你确认后继续调用模型。</small></span>
                      <button type="button" disabled={busy || active.status !== "ACTIVE"} onClick={() => submitMessage(undefined, budgetContinuation.prompt ?? "继续本次对话")}>{budgetContinuation.action_label ?? "继续本次对话"}</button>
                      <button type="button" className="advisor-draft-followup-later" disabled={busy || active.status !== "ACTIVE"} onClick={() => submitMessage(undefined, "取消继续")}>取消</button>
                    </div>
                  </section>}
                  {task && taskLabel && <section className={`advisor-task-state is-${task.status.toLowerCase()}`} aria-label={`${taskLabel.kind}任务状态`}>
                    <div><strong>{taskLabel.kind}</strong><span>{taskLabel.status}</span></div>
                    <small>第 {task.turn_number} 轮{task.answers.length ? ` · 已记录 ${task.answers.length} 条补充` : ""}</small>
                    {taskLabel.nextStep && <p><strong>接下来</strong>{taskLabel.nextStep}</p>}
                  </section>}
                  {isLatestAssistantMessage && task?.status === "WAITING_USER" && task.question && <section className="advisor-pending-question" aria-label="等待你补充">
                    <div><strong>{confirmationOptions.length ? "选一个继续" : "这一步需要你来决定"}</strong><p>{task.question}</p><small>当前任务：{task.goal}</small><small>{/(?:写入行程|保存到行程|提交到行程|应用到(?:正式)?行程)/.test(task.question) ? "这是正式行程写入确认。确认后会使用当前已选地点和已确认时间；信息不全时会直接告诉你缺什么。" : confirmationOptions.length ? "点击即可继续当前任务；生成草案后会明确显示预览和写入状态。" : "点选即可继续原任务；也可以在下方直接补充自己的想法。"}</small></div>
                    {confirmationOptions.length > 0 && <div className="advisor-pending-quick-replies" aria-label="可选操作">
                      {confirmationOptions.map((option) => <button key={option.label} type="button" disabled={busy || active.status !== "ACTIVE"} onClick={() => {
                        if (isAffirmativeWriteConfirmation(task, option)) {
                          void confirmTaskWrite(task);
                          return;
                        }
                        const candidateIds = candidateIdsForTaskConfirmation(task, option.answer, active.candidates);
                        const nextSelection = candidateIds
                          ? [...new Set([...selected, ...candidateIds])]
                          : undefined;
                        if (nextSelection) setSelected(nextSelection);
                        void submitMessage(undefined, option.answer, nextSelection);
                      }}>{option.label}</button>)}
                    </div>}
                    {!confirmationOptions.length && (/时间|几点|时段/.test(task.question) || /用餐.{0,6}(?:何时|什么时候)/.test(task.question)) && <div className="advisor-pending-quick-replies" aria-label="常用时间选项">
                      {["A · 11:00–12:00", "B · 12:00–13:00"].map((choice) => <button key={choice} type="button" disabled={busy || active.status !== "ACTIVE"} onClick={() => void submitMessage(undefined, choice.replace(/^[A-B] · /, "方案 "))}>{choice}</button>)}
                      <button type="button" className="advisor-pending-switch" disabled={busy || active.status !== "ACTIVE"} onClick={() => {
                        setMessage("其他时间：");
                        const composer = document.getElementById("advisor-message");
                        composer?.scrollIntoView({ behavior: "smooth", block: "center" });
                        composer?.focus();
                      }}>C · 其他时间</button>
                    </div>}
                    <div>{!confirmationOptions.length && <button type="button" disabled={busy || active.status !== "ACTIVE"} onClick={() => {
                      const composer = document.getElementById("advisor-message");
                      composer?.scrollIntoView({ behavior: "smooth", block: "center" });
                      composer?.focus();
                    }}>自定义回答</button>}<button type="button" className="advisor-pending-switch" disabled={busy || active.status !== "ACTIVE" || !message.trim()} onClick={() => void switchPendingTask()}>改问其他问题</button><button type="button" className="advisor-pending-cancel" disabled={busy || active.status !== "ACTIVE"} onClick={() => void submitMessage(undefined, "取消")}>取消这项咨询</button></div>
                  </section>}
                  {item.role === "assistant" && responseSource && <small className={`advisor-response-source${responseSource === "TEMPLATE" ? " is-fallback" : ""}`}>
                    {responseSource === "MODEL" ? `由 ${responseModel ?? "已配置模型"} 回复` : fallbackReason ? `本轮使用本地回复 · ${({ NOT_CONFIGURED: "模型未配置", TIMEOUT: "模型请求超时", EMPTY_RESPONSE: "模型返回空内容", INVALID_JSON_OR_SCHEMA: "模型回复格式异常", UPSTREAM_ERROR: "模型服务暂不可用", INVALID_RESPONSE: "模型返回内容异常", OUTPUT_TOKEN_LIMIT: "模型回复超过本轮预算", TOKEN_BUDGET_TOO_SMALL: "本轮模型预算不足", EXTRACTION_FAILED: "模型识别失败", UNVERIFIED_PERSISTENCE_CLAIM: "未执行保存操作" } as Record<string, string>)[fallbackReason] ?? "模型回复未完成"}` : "顾问整理"}
                  </small>}
                  {trace && <details className="advisor-agent-trace" open={trace.status === "FAILED" || trace.status === "REJECTED" || trace.status === "PARTIAL" || trace.status === "NEEDS_USER_INPUT"}>
                    <summary><span className="advisor-agent-pulse" />查看顾问处理进度</summary>
                    <ol>
                      <li><strong>已了解</strong><span>读取这段旅行和你刚才的需求</span></li>
                      {trace.recovery?.status === "SAME_TASK_SAFE_RETRY" && <li>
                        <strong>安全恢复</strong>
                        <span>{trace.recovery.interrupted_stage
                          ? `检测到上次在${advisorTurnStageLabel(trace.recovery.interrupted_stage)}阶段中断${trace.recovery.completed_stages?.length ? `；上次已完成${trace.recovery.completed_stages.map(advisorTurnStageLabel).join("、")}` : ""}；这些数据会重新核验后继续`
                          : `检测到同一任务曾中断${trace.recovery.completed_stages?.length ? `；上次已完成${trace.recovery.completed_stages.map(advisorTurnStageLabel).join("、")}` : ""}；本轮从当前旅行数据和有效检查点安全重试`}</span>
                      </li>}
                      {trace.decision_summary && <li><strong>{trace.decision_source === "MODEL" ? "Agent 选择" : "安全执行策略"}</strong><span>{trace.decision_summary}</span></li>}
                      {trace.turn_orchestration && (trace.turn_orchestration.decision_history?.length
                        ? trace.turn_orchestration.decision_history.map((decision, index) => <li key={`stage-choice-${index}`}>
                          <strong>{decision.decision_source === "MODEL" ? `Agent 选择 ${index + 1}` : decision.decision_source === "RECOVERED" ? `恢复选择 ${index + 1}` : `安全顺序 ${index + 1}`}</strong>
                          <span>{decision.decision_summary}{decision.order?.length ? ` · ${decision.order.map(advisorTurnStageLabel).join(" → ")}` : ""}{decision.fallback_reason ? ` · ${decision.fallback_reason}` : ""}</span>
                        </li>)
                        : <li>
                          <strong>{trace.turn_orchestration.decision_source === "MODEL" ? "Agent 安排步骤" : trace.turn_orchestration.decision_source === "RECOVERED" ? "恢复 Agent 步骤" : "安全执行顺序"}</strong>
                          <span>{trace.turn_orchestration.decision_summary}{trace.turn_orchestration.order?.length ? ` · ${trace.turn_orchestration.order.map(advisorTurnStageLabel).join(" → ")}` : ""}</span>
                        </li>)}
                      {trace.stage_statuses && trace.stage_statuses.length > 0 && <li>
                        <strong>本轮处理阶段</strong>
                        <span>{trace.stage_statuses.map((stage) => `${stage.stage === "SEARCH_WEATHER" ? "地点与天气" : stage.stage === "ROUTE" ? "路线" : stage.stage === "SCHEDULE" ? "行程安排" : stage.stage}：${advisorStageStatusLabel(stage.status)}`).join(" · ")}</span>
                      </li>}
                      {evidenceSummary && <li>
                        <strong>地点证据</strong>
                        <span>{evidenceSummary.total} 个本轮候选{evidenceSummary.providers.length ? ` · ${evidenceSummary.providers.join("、")}` : ""} · {evidenceSummary.verified ? `${evidenceSummary.verified} 个已核验` : "无真实核验结果"}{evidenceSummary.simulated ? ` · ${evidenceSummary.simulated} 个模拟结果` : ""}。候选只代表地图检索结果，不代表全网热度或营业状态。</span>
                      </li>}
                      {transitions.map((step, index) => <li key={`${step.tool}-${index}`}>
                          <strong>{step.stage === "SEARCH_WEATHER" ? "地点与天气 · " : step.stage === "ROUTE" ? "路线 · " : step.stage === "SCHEDULE" ? "行程安排 · " : ""}{step.tool === "get_trip_weather" ? "天气预报" : step.tool === "get_distance_matrix" ? "行程交通核验" : step.tool === "get_trip_route" ? "两地路线查询" : step.tool === "build_schedule_draft" ? "确定性排程器" : step.tool === "search_places" ? "地点搜索" : step.tool === "search_destination_places" ? "目的地探索" : "信息检查"}</strong>
                          <span>{step.status === "VERIFIED" ? step.tool === "build_schedule_draft" ? "草案通过结构校验" : "已完成核对" : step.status === "FAILED" ? "暂时失败，可调整条件后重试" : step.status === "REJECTED" ? "信息不完整，需要你确认" : "处理中"}</span>
                      </li>)}
                      <li><strong>下一步</strong><span>{trace.status === "NEEDS_USER_INPUT" ? "请回答顾问提出的问题" : routeUnavailable ? "路线暂未核实，安排前请留意交通时间" : "你可以选择地点并检查安排；确认后才会保存"}</span></li>
                    </ol>
                  </details>}
                  {item.role === "assistant" && item.content.trim() && <section className="advisor-message-feedback" aria-label="评价顾问回复">
                    <div className="advisor-message-feedback-heading">
                      <span>{storedFeedback?.rating === "HELPFUL" ? "已反馈：这条回复有帮助" : storedFeedback?.rating === "NOT_HELPFUL" ? "已反馈：这条回复需要改进" : "这条回复对你有帮助吗？"}</span>
                      {storedFeedback?.rating === "NOT_HELPFUL" && storedFeedback.reason && <small>原因：{ADVISOR_FEEDBACK_REASON_LABELS[storedFeedback.reason]}</small>}
                    </div>
                    <div className="advisor-message-feedback-actions">
                      <button type="button" aria-pressed={storedFeedback?.rating === "HELPFUL"} disabled={feedbackSavingMessageId === item.id} onClick={() => void saveAdvisorMessageFeedback(item.id, "HELPFUL")}>
                        {feedbackSavingMessageId === item.id ? "保存中…" : storedFeedback?.rating === "HELPFUL" ? "有帮助 ✓" : "有帮助"}
                      </button>
                      <button type="button" aria-pressed={storedFeedback?.rating === "NOT_HELPFUL"} disabled={feedbackSavingMessageId === item.id} onClick={() => setFeedbackReasonMessageId((current) => current === item.id ? null : item.id)}>
                        {storedFeedback?.rating === "NOT_HELPFUL" ? "修改反馈" : "没帮助"}
                      </button>
                    </div>
                    {feedbackReasonMessageId === item.id && <div className="advisor-message-feedback-reasons">
                      <small>可选：主要问题是什么？</small>
                      {(Object.entries(ADVISOR_FEEDBACK_REASON_LABELS) as [AdvisorMessageFeedbackReason, string][]).map(([reason, label]) => <button type="button" key={reason} disabled={feedbackSavingMessageId === item.id} onClick={() => void saveAdvisorMessageFeedback(item.id, "NOT_HELPFUL", reason)}>{label}</button>)}
                      <button type="button" className="advisor-message-feedback-skip" disabled={feedbackSavingMessageId === item.id} onClick={() => void saveAdvisorMessageFeedback(item.id, "NOT_HELPFUL")}>只提交评价</button>
                    </div>}
                  </section>}
                  {transportRoute && <AdvisorRouteResult route={transportRoute} />}
                </article>;
              })}
              {busy && <div className="advisor-agent-running" role="status" aria-live="polite">
                <span className="advisor-agent-pulse" />
                <div><strong>{agentProgress.length ? progressTitle(agentProgress.at(-1)!) : agentActivity}</strong>
                  {agentProgress.length > 0 && <ol>{agentProgress.slice(-4).map((item) => <li key={item.id}>{progressTitle(item)}</li>)}</ol>}
                </div>
              </div>}
              {!active.messages.length && <p className="advisor-empty">告诉顾问你想去的地点或偏好。</p>}
            </div>
            <div
              className="advisor-message-resizer"
              role="separator"
              aria-label="调整对话消息区域高度"
              title="拖动调整对话高度；双击恢复默认"
              aria-orientation="horizontal"
              aria-valuemin={360}
              aria-valuemax={1000}
              aria-valuenow={chatMessagesHeight}
              tabIndex={0}
              onPointerDown={(event) => {
                event.preventDefault();
                resizingAdvisorMessagesRef.current = true;
                event.currentTarget.setPointerCapture(event.pointerId);
              }}
              onPointerMove={resizeAdvisorMessages}
              onPointerUp={finishAdvisorMessageResize}
              onPointerCancel={finishAdvisorMessageResize}
              onDoubleClick={() => {
                const defaultHeight = window.innerWidth <= 1100 ? 500 : 620;
                setChatMessagesHeight(defaultHeight);
                window.localStorage.setItem("travelpilot-advisor-message-height", String(defaultHeight));
              }}
              onKeyDown={(event) => {
                if (event.key !== "ArrowUp" && event.key !== "ArrowDown" && event.key !== "Home" && event.key !== "End") return;
                event.preventDefault();
                const nextHeight = event.key === "Home" ? 360 : event.key === "End" ? 1000
                  : Math.max(360, Math.min(1000, chatMessagesHeight + (event.key === "ArrowDown" ? 40 : -40)));
                setChatMessagesHeight(nextHeight);
                window.localStorage.setItem("travelpilot-advisor-message-height", String(nextHeight));
              }}
            ><span>拖动调整对话高度</span><i aria-hidden="true" /></div>
            {destinationRecommendations.length > 0 && !active.destination && <section className="advisor-destination-results" aria-label="目的地候选">
              <div><strong>地图证据支持的目的地</strong><small>候选来自高德地点检索{destinationSearchCount !== null ? ` · 本轮查询 ${destinationSearchCount} 组` : ""} · {destinationRecommendations[0].retrieved_at?.slice(0, 16).replace("T", " ")}</small>
                {destinationSearchCount !== null && !destinationSearchScopeComplete && <p>这是 Agent 根据已查询证据收敛出的候选，不代表已比较全部城市、交通或费用。你可以补充偏好后再次探索。</p>}</div>
              <div className="advisor-destination-grid">{destinationRecommendations.map((item) => <article key={item.city}>
                <h3>{item.city}</h3><p>{item.reasons.join("；")}</p>
                <ul>{item.places.slice(0, 3).map((place) => <li key={place.provider_id}>{place.name}{place.popularity_score != null && <strong className="advisor-map-rating">地图评分 {place.popularity_score.toFixed(1)}</strong>}<small>{place.category}{place.address ? ` · ${place.address}` : ""}</small></li>)}</ul>
                <button type="button" className="secondary" onClick={() => chooseDestination(item.city)} disabled={busy || active.status !== "ACTIVE"}>选择这个城市</button>
              </article>)}</div>
            </section>}
            <div className="advisor-quick-prompts">{activeCandidateDomain === "FOOD" ? <><button type="button" disabled={busy || active.status !== "ACTIVE"} onClick={() => submitMessage(undefined, "再找几家本地客家菜馆")}>本地特色菜</button><button type="button" disabled={busy || active.status !== "ACTIVE"} onClick={() => submitMessage(undefined, "优先推荐人均实惠的午餐")}>人均实惠</button><button type="button" disabled={busy || active.status !== "ACTIVE"} onClick={() => submitMessage(undefined, "请避开辣味和油腻的餐厅")}>清淡口味</button></> : <>{active.preference_text && <button type="button" disabled={busy || active.status !== "ACTIVE" || !active.destination} onClick={() => submitMessage(undefined, `请按我的偏好搜索地图候选：${active.preference_text}`)}>按当前偏好找候选</button>}<button type="button" disabled={busy || active.status !== "ACTIVE"} onClick={() => submitMessage(undefined, "我喜欢节奏轻松一些，多留休息时间")}>节奏轻松一点</button><button type="button" disabled={busy || active.status !== "ACTIVE"} onClick={() => submitMessage(undefined, "我想去宽窄巷子")}>示例：搜索宽窄巷子</button></>}</div>
            <form className="advisor-composer" onSubmit={(event) => { void submitMessage(event); }}>
              <label className="sr-only" htmlFor="advisor-message">告诉顾问你的想法</label>
              <textarea id="advisor-message" value={message} onChange={(event) => setMessage(event.target.value)} placeholder={activeCandidateDomain === "FOOD" ? "还想吃什么、预算多少、需要避开什么？" : "说说你想去哪里、喜欢什么，或想调整的安排…"} rows={2} disabled={busy || active.status !== "ACTIVE"} />
              <button className="primary" type="submit" disabled={!message.trim() || busy || active.status !== "ACTIVE"}>发送</button>
            </form>
          </> : <div className="advisor-welcome">
            <span className="advisor-welcome-mark" aria-hidden="true">✳</span>
            <h3>不用先想好完整路线。</h3>
            <p>可以先聊偏好，也可以直接填写城市和日期。条件补齐后，地图候选由你选择，再生成可确认的行程。</p>
            <div className="advisor-welcome-prompts"><button type="button" onClick={() => focusPlanningSetup("destination", "喜欢本地美食和老街巷，行程节奏轻松一些")}>想慢慢逛，看看本地生活 <span>↗</span></button><button type="button" onClick={() => focusPlanningSetup("destination", "我会逐个告诉你想去的地点，请帮我合理安排顺序")}>我已经有几个想去的地方 <span>↗</span></button><button type="button" onClick={() => focusPlanningSetup("existing")}>调整一趟已经创建的旅行 <span>↗</span></button></div>
          </div>}
        </section>

        {!chatExpanded && <div
          className="advisor-column-resizer"
          role="separator"
          aria-label="调整对话和规划区域宽度"
          aria-orientation="vertical"
          aria-valuemin={35}
          aria-valuemax={65}
          aria-valuenow={chatRatio}
          tabIndex={0}
          onPointerDown={(event) => {
            if (window.innerWidth <= 1100) return;
            event.preventDefault();
            resizingAdvisorColumnsRef.current = true;
            event.currentTarget.setPointerCapture(event.pointerId);
          }}
          onPointerMove={resizeAdvisorColumns}
          onPointerUp={finishAdvisorColumnResize}
          onPointerCancel={finishAdvisorColumnResize}
          onDoubleClick={() => {
            setChatRatio(50);
            window.localStorage.setItem("travelpilot-advisor-chat-ratio", "50");
          }}
          onKeyDown={(event) => {
            if (event.key !== "ArrowLeft" && event.key !== "ArrowRight" && event.key !== "Home" && event.key !== "End") return;
            event.preventDefault();
            const nextRatio = event.key === "Home" ? 35 : event.key === "End" ? 65
              : Math.max(35, Math.min(65, chatRatio + (event.key === "ArrowRight" ? 2 : -2)));
            setChatRatio(nextRatio);
            window.localStorage.setItem("travelpilot-advisor-chat-ratio", String(nextRatio));
          }}
        />}

        <div className="advisor-plan" style={{ maxHeight: advisorChatPanelHeight ? `${advisorChatPanelHeight}px` : undefined }}>
          <section className="advisor-preferences panel">
            <div className="advisor-panel-title"><div><h2>{showingMixedRecommendation ? "多类型旅行候选" : activeCandidateDomain === "FOOD" ? "美食与用餐安排" : activeCandidateDomain === "LODGING" ? "住宿候选" : "地图地点候选"}</h2><p>{showingMixedRecommendation ? "景点、餐饮等候选会分别标注；选中后再为每项确认日期与时间。" : activeCandidateDomain === "FOOD" ? "推荐结果只包含餐饮；选择后请确认用餐日期和时间。" : activeCandidateDomain === "LODGING" ? "住宿只加入备选清单，不会占用每日游览时间，也不代表已预订。" : "选择地点后确认日期和开始时间；地图提供的游览时长仅作估算。"}</p><p className="advisor-preview-autosave" role="status">{autoPreviewPending ? "正在自动核验并更新右侧预览…" : "选定地点并补齐日期、时间后，右侧预览会自动更新；确认后才写入正式行程。"}</p></div></div>
            <div className="advisor-candidate-toolbar">
              <div className="advisor-candidate-filters" role="group" aria-label="筛选候选地点">
                {([ ["AUTO", "当前推荐"], ["ALL", "全部"], ["ATTRACTION", `游玩 ${candidateCount("ATTRACTION")}`], ["FOOD", `美食 ${candidateCount("FOOD")}`], ["LODGING", `住宿 ${candidateCount("LODGING")}`] ] as const).map(([filter, label]) => <button key={filter} type="button" className={candidateFilter === filter ? "is-active" : ""} aria-pressed={candidateFilter === filter} onClick={() => setCandidateFilter(filter)}>{label}</button>)}
              </div>
              <div className="advisor-selection-count" aria-live="polite">已选 {attractionSelectionCount} 个游玩 · {foodSelectionCount} 个用餐 · {lodgingSelectionCount} 个住宿</div>
              {displayedCandidates.length > 0 && <button type="button" className="advisor-map-toggle" aria-expanded={candidateMapOpen} onClick={() => setCandidateMapOpen((value) => !value)}>{candidateMapOpen ? "收起地图" : "地图查看"}</button>}
            </div>
            {displayedCandidates.length > 0 && <p className="advisor-candidate-evidence-note">来源时间表示地图数据回传时间；不代表实时营业、价格或全网热度已核实。</p>}
            {candidateMapOpen && displayedCandidates.length > 0 && <div className="advisor-candidate-map"><RouteMap title="候选地点分布" points={displayedCandidates.map((candidate) => ({ id: candidate.id, name: candidate.name, detail: planningCategoryLabel(candidate.planning_category, candidate.meal_type), latitude: candidate.latitude, longitude: candidate.longitude }))} disclaimer="地图展示候选地点位置，不代表已规划道路路线。" /></div>}
            {displayedCandidates.length ? <div className="advisor-live-candidates">{displayedCandidates.map((candidate) => {
              const schedule = schedules[candidate.id] ?? { day: "", start_time: "" };
              const domainLabel = planningCategoryLabel(candidate.planning_category, candidate.meal_type);
              const candidateSource = candidate.verification_status === "VERIFIED"
                ? candidate.provider === "amap" ? "高德地图" : "地图服务"
                : candidate.verification_status === "SIMULATED" || candidate.provider === "fake"
                  ? "本地模拟数据" : "旅行中已保存";
              const evidenceTime = candidate.verified_at
                ? ` · ${candidate.verification_status === "SIMULATED" ? "模拟数据时间" : "最近核验"} ${candidate.verified_at.slice(0, 16).replace("T", " ")}`
                : "";
              return <article className="advisor-live-candidate" id={`advisor-candidate-${candidate.id}`} key={candidate.id}>
                <div className="advisor-live-candidate-main">
                <div className="advisor-live-candidate-choice">
                  <span><strong>{candidate.name}{domainLabel && <em>{domainLabel}</em>}{candidate.in_current_plan ? <em>当前计划</em> : null}{candidate.popularity_score != null && <em>地图评分 {candidate.popularity_score.toFixed(1)}</em>}</strong><small>来源：{candidateSource}{evidenceTime}</small><small>地图类别：{candidate.category} · {candidate.address || "地址未提供"} · 预计停留 {candidate.duration_min} 分钟</small>{candidate.guide_references?.map((reference) => <small className="advisor-guide-reference" key={`${candidate.id}:${reference.source_id}`}>攻略线索：《{reference.title}》提到：{reference.guide_claim} · 尚未核验</small>)}{candidate.suggestion && <small>{candidate.suggestion}</small>}{candidate.profile_memory_conflicts?.map((conflict) => <small className="advisor-memory-conflict" key={conflict}>与已确认的旅行限制冲突：{conflict}。如果这次仍想去，可以在生成草案时明确确认本次继续。</small>)}</span>
                </div>
                <button type="button" className={`advisor-candidate-action${selected.includes(candidate.id) ? " is-selected" : ""}`} onClick={() => toggleCandidate(candidate.id)} disabled={active?.status !== "ACTIVE"}>{selected.includes(candidate.id) ? "已加入 ✓" : candidate.planning_category === "FOOD" ? `加入${planningCategoryLabel("FOOD", candidate.meal_type)}` : candidate.planning_category === "LODGING" ? "加入住宿" : "加入行程"}</button>
                </div>
                {selected.includes(candidate.id) && (candidate.planning_category === "LODGING" ? <p className="advisor-lodging-note">已加入住宿清单，不会占用每日游览时间；这不代表已预订。</p> : <div className="advisor-candidate-schedule"><label>{candidate.planning_category === "FOOD" ? "用餐日期" : "安排日期"}<select value={schedule.day} onChange={(event) => updateCandidateSchedule(candidate.id, "day", event.target.value)}><option value="">请选择日期</option>{travelDays.map((day, index) => <option key={day} value={day}>第 {index + 1} 天 · {day}</option>)}</select></label><label>{candidate.planning_category === "FOOD" ? "用餐时间" : "开始时间"}<input type="time" value={schedule.start_time} onChange={(event) => updateCandidateSchedule(candidate.id, "start_time", event.target.value)} /></label>{candidate.explicit_day || candidate.explicit_start_time ? <small>根据你在对话中的明确表述预填，可自行修改。</small> : candidate.in_current_plan && schedule.day && schedule.start_time ? <small>沿用当前行程已有时间（{schedule.day} {schedule.start_time}）；你可以在这里调整。</small> : <small>还没有确认日期和时间，请你选择；系统不会替你决定。</small>}</div>)}
              </article>;
            })}</div> : <p className="advisor-empty">{active && !active.destination ? "先在旅行条件中补充目的地城市，再按偏好搜索候选。" : "还没有地点候选。你可以说“推荐几个热门地点”，也可以告诉顾问城市和兴趣类别。"}</p>}
            {active?.draft && <div className="advisor-draft-summary"><strong>行程调整预览 · 第 {active.draft.version} 版</strong><span>{active.draft.stops.length} 个时间安排</span>{active.draft.lodging?.length ? <span>{active.draft.lodging.length} 个住宿备选</span> : null}<span>{active.draft.warnings.length} 条排程提示</span></div>}
            {active?.draft?.applied_personal_memories?.length ? <p className="advisor-context-note">本次采用的已确认偏好：{active.draft.applied_personal_memories.map((memory) => memory.text).join("；")}</p> : null}
            {active?.draft?.transport && <small className="advisor-context-note">交通时间：{active.draft.transport.status === "VERIFIED" ? `${active.draft.transport.source} 已核验` : active.draft.transport.status === "SIMULATED" ? "本地模拟数据" : "仅安排单个地点，无需地点间交通矩阵"}{active.draft.transport.retrieved_at ? ` · ${active.draft.transport.retrieved_at.slice(0, 16).replace("T", " ")}` : ""}</small>}
            {active?.draft && <div className="advisor-live-diff" aria-label="草案变更摘要">
              <strong>相对当前计划的变化</strong>
              <span>新增 {active.draft.change_summary.add ?? 0}</span><span>移除 {active.draft.change_summary.remove ?? 0}</span>
              <span>调整时间 {active.draft.change_summary.move ?? 0}</span><span>保留 {active.draft.change_summary.keep ?? 0}</span>
              {active.draft.pace_change && <span>节奏 {active.draft.pace_change.before} → {active.draft.pace_change.after}</span>}
              {active.draft.trip_changes && Object.entries(active.draft.trip_changes.after).filter(([key, value]) => active.draft?.trip_changes?.before[key] !== value).map(([key, value]) => <span key={key}>{({ destination: "目的地", start_date: "开始日期", end_date: "结束日期", budget: "预算", pace_level: "节奏" } as Record<string, string>)[key] ?? key}：{String(active.draft?.trip_changes?.before[key] ?? "未设置")} → {String(value ?? "未设置")}</span>)}
              {active.draft.changes.filter((change) => change.operation !== "KEEP").map((change) => <div key={`${change.stable_key}-${change.operation}`} className={`advisor-live-diff-row ${change.operation.toLowerCase()}`}>
                <b>{({ ADD: "新增", REMOVE: "移除", MOVE: "调整" } as Record<string, string>)[change.operation]}</b><span>{change.name}</span>
                {change.before && <small>原：{String(change.before.day)} {String(change.before.start_time).slice(0, 5)}</small>}
                {change.after && <small>新：{String(change.after.day)} {String(change.after.start_time).slice(0, 5)}</small>}
              </div>)}
            </div>}
          </section>

          <section className="advisor-itinerary panel" id="advisor-itinerary-preview" tabIndex={-1}>
            <div className="advisor-panel-title"><div><h2>行程调整预览</h2><p>{active?.destination || selectedTrip?.destination || "尚未设置目的地"} · {active?.start_date || selectedTrip?.start_date || "日期待定"} 至 {active?.end_date || selectedTrip?.end_date || "日期待定"}</p></div><span className="advisor-draft-state">{active?.status === "CONFIRMED" ? "已写入正式行程" : autoPreviewPending ? "正在自动更新" : active?.draft ? draftIsStale ? "正在准备更新预览" : "等待你确认" : "尚未生成"}</span></div>
            {active?.candidates.filter((candidate) => selected.includes(candidate.id)
              && !active.draft?.selected_candidate_ids.includes(candidate.id)
              && !candidate.in_current_plan).map((candidate) => <section className="advisor-selected-candidate-live" role="status" key={`pending:${candidate.id}`}>
                <div><strong>已选{planningCategoryLabel(candidate.planning_category, candidate.meal_type)}：{candidate.name}</strong>
                  <small>{candidate.planning_category === "LODGING"
                    ? "已加入住宿备选；右侧预览会自动同步。"
                    : "已加入本次选择；补齐日期和时间后会自动排入右侧预览。"}</small></div>
                <button type="button" className="secondary" onClick={() => document.getElementById(`advisor-candidate-${candidate.id}`)?.scrollIntoView({ behavior: "smooth", block: "center" })}>查看并调整</button>
              </section>)}
            {active?.draft && draftIsStale && <p className="advisor-live-warning" role="status">你的选择已保存，正在自动重新核验预览；完成后午餐、晚餐和其他安排会同步显示。正式行程仍需最后确认。</p>}
            {draftDays.length > 1 && <label className="advisor-live-day-picker">地图日期<select value={visibleMapDay} onChange={(event) => setMapDay(event.target.value)}>{draftDays.map((day, index) => <option key={day} value={day}>第 {index + 1} 天 · {day}</option>)}</select><small>仅切换下方地图；行程安排按日期分组全部显示。</small></label>}
            {active?.draft?.stops.length ? <div className="advisor-stop-list">{draftDays.map((day) => {
              const dayStops = active.draft?.stops.filter((stop) => stop.day === day) ?? [];
              const dayIndex = travelDays.indexOf(day) + 1;
              return <section className="advisor-day-group" key={day} aria-label={`第 ${dayIndex} 天 ${day}`}>
                <h3 className="advisor-day-heading">第 {dayIndex > 0 ? dayIndex : "—"} 天 <time dateTime={day}>{day}</time></h3>
                {dayStops.map((stop, index) => {
                  const previous = dayStops[index - 1];
                  const transition = previous && active.draft?.transitions?.find((item) => item.from_candidate_id === previous.candidate_id && item.to_candidate_id === stop.candidate_id);
                  return <div className="advisor-stop-sequence" key={`${stop.candidate_id}-${stop.day}`}>
                    {transition && <p className="advisor-transport-leg" aria-label={`${previous.name} 到 ${stop.name} 交通约 ${transition.duration_min} 分钟`}><span aria-hidden="true">↘</span><strong>{transition.status === "VERIFIED" ? "地图路线" : transition.status === "SIMULATED" ? "模拟路线" : "距离估算"}</strong> {previous.name} → {stop.name} · 约 {transition.duration_min} 分钟{transition.source ? ` · ${transition.source}` : ""}</p>}
                    <article className="advisor-stop">
                      <span className="advisor-stop-index">{index + 1}</span><div className="advisor-stop-time">{stop.start_time.slice(0, 5)}<br /><span>{stop.end_time.slice(0, 5)}</span></div>
                      <div className="advisor-stop-content"><h3>{stop.name}</h3><small>{planningCategoryLabel(stop.planning_category, stop.meal_type) || stop.category}</small>{stop.note && <small>{stop.note}</small>}<p>{stop.address || "地图候选地点"}</p></div>
                    </article>
                  </div>;
                })}
              </section>;
            })}</div> : <p className="advisor-empty">选择地点并生成草案后，这里会按天显示游玩、餐饮和地点间交通安排。</p>}
            {active?.draft?.stops.length ? <div className="advisor-live-map"><RouteMap title="当日真实地点地图" points={mapStops.map((stop) => ({ id: stop.candidate_id, name: stop.name, detail: `${stop.start_time.slice(0, 5)}–${stop.end_time.slice(0, 5)}`, latitude: stop.latitude, longitude: stop.longitude }))} disclaimer="编号与草案中的游览顺序一致。" showDrivingRoute={mapStops.length > 1} /></div> : null}
            {active?.draft?.lodging?.length ? <section className="advisor-lodging-summary"><strong>住宿备选 · 尚未预订</strong>{active.draft.lodging.map((place) => <span key={place.candidate_id}>{place.name}{place.address ? ` · ${place.address}` : ""}{place.verification_status === "VERIFIED" ? " · 地图已核验" : " · 待核实"}</span>)}</section> : null}
            {active?.draft?.origin_leg && active.draft.origin_leg.status !== "NOT_PROVIDED" && <p className="advisor-context-note">
              {active.draft.origin_leg.status === "VERIFIED" || active.draft.origin_leg.status === "SIMULATED"
                ? `首日出发交通：${active.draft.origin_leg.from} → 首个地点，约 ${active.draft.origin_leg.duration_min ?? "—"} 分钟 · ${active.draft.origin_leg.source ?? "地图数据"}`
                : "出发地未能唯一核验或交通查询不可用；草案没有把这段时间计入排程。"}
            </p>}
            {active?.draft?.weather && <div className="advisor-weather-summary">
              <strong>天气参考 · {active.draft.weather.source}</strong>
              {active.draft.weather.days.length ? <div className="advisor-weather-days">{active.draft.weather.days.map((day) => <span key={day.date}>
                <b>{day.date.slice(5)}</b> {day.description} · {day.temperature_min_c ?? "—"}°–{day.temperature_max_c ?? "—"}°
                {day.precipitation_probability_max != null ? ` · 降水 ${day.precipitation_probability_max}%` : ""}
                {day.advice?.map((tip) => <small className="advisor-weather-tip" key={tip.kind}>{tip.text}</small>)}
                {day.advice?.some((tip) => tip.kind === "INDOOR_ALTERNATIVE")
                  && !active?.draft?.weather?.plan_impacts?.some((impact) => impact.day === day.date)
                  && <button type="button" className="advisor-weather-alternative" disabled={busy || active.status !== "ACTIVE"} onClick={() => { const dayIndex = travelDays.indexOf(day.date) + 1; void submitMessage(undefined, `针对第${dayIndex || ""}天的天气，请为户外安排推荐室内替代地点`); }}>找室内备选</button>}
              </span>)}</div> : <p>{active.draft.weather.message || "当前没有可用预报。"}</p>}
              {!!active.draft.weather.plan_impacts?.length && <div className="advisor-weather-impacts">
                <b>这些已安排的户外地点值得留意</b>
                {active.draft.weather.plan_impacts.map((impact) => <p key={`${impact.candidate_id}:${impact.day}`}>
                  {impact.day} · {impact.name}：{impact.reason}
                </p>)}
                <small>处理入口位于“接下来可以一起补齐”；地图不能确认场所是否室内或当天开放，行程不会自动替换或保存。</small>
              </div>}
              <small>{active.draft.weather.attribution} · 更新于 {active.draft.weather.fetched_at.slice(0, 16).replace("T", " ")}</small>
            </div>}
            {active?.draft && (active.draft.attention_items?.length || active.status === "CONFIRMED") ? <section className="advisor-attention-panel" aria-label={active.status === "CONFIRMED" ? "行程保存检查结果" : "需要你决定的行程空档"}>
              <div className="advisor-attention-heading"><span aria-hidden="true">✳</span><div><h3>{active?.status === "CONFIRMED" ? currentAttentionItems.length ? "行程已保存，仍有可选完善项" : "行程已保存，检查结果已同步" : currentAttentionItems.length ? "有几段安排还没确定" : "当前缺项已补齐"}</h3><p>{active?.status === "CONFIRMED" ? currentAttentionItems.length ? "正式行程已更新；以下是仍未安排的可选项目。" : "正式行程已更新，当前安排与检查结果一致。" : "你来决定怎么处理，正式行程不会在你确认前改变。"}</p></div></div>
              {selectedUnscheduledMealCandidates.length > 0 && <p className="advisor-attention-selection-note" role="status">
                已选餐饮地点：{selectedUnscheduledMealCandidates.map((candidate) => candidate.name).join("、")}。还需选择用餐日期和时间；顾问不会替你填默认值。<button type="button" className="advisor-attention-inline-action" onClick={() => {
                  setCandidateFilter("FOOD");
                  window.setTimeout(() => document.getElementById(`advisor-candidate-${selectedUnscheduledMealCandidates[0].id}`)?.scrollIntoView({ behavior: "smooth", block: "center" }), 0);
                }}>去设置</button>
              </p>}
              {locallyCoveredAttentionItems.length > 0 && <p className="advisor-attention-selection-note" role="status">
                已按你刚才的选择补上{locallyCoveredAttentionItems.some((item) => item.kind === "MEAL_MISSING") ? "餐饮" : ""}{locallyCoveredAttentionItems.some((item) => item.kind === "MEAL_MISSING") && locallyCoveredAttentionItems.some((item) => item.kind === "LODGING_MISSING") ? "和" : ""}{locallyCoveredAttentionItems.some((item) => item.kind === "LODGING_MISSING") ? "住宿候选" : ""}。更新预览后会重新核验；点击底部“确认并应用到行程”后才会写入正式行程。
              </p>}
              {visibleAttentionItems.length ? <ul>{visibleAttentionItems.map((item) => {
                const dayIndex = travelDays.indexOf(item.day) + 1;
                const mealLabel = item.meal_type === "LUNCH" ? "午餐" : "晚餐";
                const itemKey = checklistItemKey(item);
                const decision = active.context?.checklist_decisions?.[itemKey];
                const isHandled = decision?.decision === "NOT_NEEDED" || (decision?.decision === "LATER" && decision.draft_version === active.draft?.version);
                const label = item.kind === "MEAL_MISSING" ? `还没有安排${mealLabel}`
                  : item.kind === "OPEN_GAP" ? `${item.from}–${item.to} 有约 ${item.hours} 小时空档`
                    : item.kind === "EMPTY_DAY" ? "整天还没有安排"
                      : item.kind === "REST_CHECK" ? "当天安排跨度较长，未检测到明显休息空档"
                      : item.kind === "LODGING_MISSING" ? "多日旅行还没有住宿备选"
                        : item.kind === "ORIGIN_MISSING" ? "未设置出发地，首段交通时间无法估算"
                          : "地点间交通路线暂未核验";
                const prompt = item.kind === "MEAL_MISSING" ? `请推荐第${dayIndex || ""}天的${mealLabel}`
                  : item.kind === "OPEN_GAP" ? `第${dayIndex || ""}天${item.from}到${item.to}有空档，请推荐适合这段时间的地点`
                    : item.kind === "EMPTY_DAY" ? `第${dayIndex || ""}天目前没有安排，请帮我推荐适合的活动`
                      : item.kind === "REST_CHECK" ? `请检查第${dayIndex || ""}天的已选安排。这一天跨度较长，地点之间没有检测到45分钟以上的明显空档。请结合游览时长和交通证据，提出更轻松的待确认时间安排，保留固定预约，不要修改正式行程。`
                      : item.kind === "LODGING_MISSING" ? `请为这段旅行推荐住宿候选，先给我选择，不要自动加入行程`
                        : "请优化当前计划时间并重新核验地点之间的交通路线";
                return <li key={itemKey}>
                  <span><strong>{item.kind === "LODGING_MISSING" ? "住宿" : item.kind === "ORIGIN_MISSING" || item.kind === "TRANSPORT_UNVERIFIED" ? "交通" : `第 ${dayIndex || "—"} 天`}</strong> · {label}</span>
                  <div className="advisor-attention-item-actions">
                    {isHandled ? <button type="button" disabled={busy} onClick={() => void saveAttentionDecision(itemKey, "RESET")}>恢复检查</button> : item.kind === "ORIGIN_MISSING" ? <button type="button" disabled={busy} onClick={() => {
                      if (item.kind === "ORIGIN_MISSING") {
                        const editor = document.querySelector<HTMLDetailsElement>(".advisor-context-editor");
                        if (editor) editor.open = true;
                        window.setTimeout(() => document.getElementById("advisor-origin-input")?.focus(), 0);
                      }
                    }}>补充出发地</button> : item.kind === "TRANSPORT_UNVERIFIED" ? <button type="button" disabled={busy} onClick={() => void submitMessage(undefined, prompt)}>重新核验</button> : <button type="button" disabled={busy} onClick={() => active && setSelectedAttention({ sessionId: active.id, itemKey })}>{selectedAttentionKey === itemKey ? "已选择" : item.kind === "REST_CHECK" ? "检查休息安排" : "选择处理方式"}</button>}
                    {!isHandled && (item.kind === "ORIGIN_MISSING" || item.kind === "TRANSPORT_UNVERIFIED") && <><button type="button" className="advisor-attention-skip" disabled={busy} onClick={() => void saveAttentionDecision(itemKey, "LATER")}>稍后</button><button type="button" className="advisor-attention-skip" disabled={busy} onClick={() => void saveAttentionDecision(itemKey, "NOT_NEEDED")}>不需要</button></>}
                  </div>
                </li>;
              })}</ul> : <p className="advisor-attention-empty">{showHandledAttention ? "没有已处理的检查项。" : "当前检查项都已处理。"}</p>}
              {selectedAttentionItem && (() => {
                const item = selectedAttentionItem;
                const itemKey = checklistItemKey(item);
                const dayIndex = travelDays.indexOf(item.day) + 1;
                const mealLabel = item.meal_type === "LUNCH" ? "午餐" : "晚餐";
                const restCheck = item.kind === "REST_CHECK";
                const prompt = item.kind === "MEAL_MISSING" ? `请推荐第${dayIndex || ""}天的${mealLabel}`
                  : item.kind === "OPEN_GAP" ? `第${dayIndex || ""}天${item.from}到${item.to}有空档，请推荐适合这段时间的地点`
                    : item.kind === "EMPTY_DAY" ? `第${dayIndex || ""}天目前没有安排，请帮我推荐适合的活动`
                      : restCheck ? `请检查第${dayIndex || ""}天的已选安排。这一天跨度较长，地点之间没有检测到45分钟以上的明显空档。请结合游览时长和交通证据，提出更轻松的待确认时间安排，保留固定预约，不要修改正式行程。`
                        : `请为这段旅行推荐住宿候选，先给我选择，不要自动加入行程`;
                const label = item.kind === "MEAL_MISSING" ? `${mealLabel}还没有安排`
                  : item.kind === "OPEN_GAP" ? `${item.from}–${item.to} 的空档`
                    : item.kind === "EMPTY_DAY" ? `第 ${dayIndex || "—"} 天还是空白`
                      : item.kind === "REST_CHECK" ? `第 ${dayIndex || "—"} 天是否需要安排休息`
                      : "住宿还没有安排";
                return <div className="advisor-attention-choice" aria-live="polite">
                  <div><strong>怎么处理：{label}？</strong><button type="button" className="advisor-attention-close" aria-label="关闭处理选项" onClick={() => setSelectedAttention(null)}>收起</button></div>
                  <p>{restCheck ? "顾问只会先生成更轻松的待确认安排；也可以保留现状或稍后处理。" : "你可以让顾问按这段时间推荐，也可以导入自己的攻略；现在不决定也没关系。"}</p>
                  <div className="advisor-attention-choice-actions">
                    <button type="button" disabled={busy} onClick={async () => { if (await saveAttentionDecision(itemKey, "LATER")) { setSelectedAttention(null); void submitMessage(undefined, prompt); } }}>{restCheck ? "调整得轻松一点" : "让顾问推荐"}</button>
                    {!restCheck && <button type="button" className="secondary" disabled={!active.trip_id} title={!active.trip_id ? "确认创建这段旅行后即可导入攻略" : undefined} onClick={() => setImportOpen(true)}>从攻略导入</button>}
                    <button type="button" className="secondary" disabled={busy} onClick={async () => { if (await saveAttentionDecision(itemKey, "LATER")) setSelectedAttention(null); }}>稍后</button>
                    <button type="button" className="secondary" disabled={busy} onClick={async () => { if (await saveAttentionDecision(itemKey, "NOT_NEEDED")) setSelectedAttention(null); }}>{restCheck ? "保留当前安排" : "这次不需要"}</button>
                  </div>
                </div>;
              })()}
              <div className="advisor-attention-actions"><button type="button" className="secondary" onClick={() => setShowHandledAttention((value) => !value)}>{showHandledAttention ? "查看待处理项" : `查看已处理项 (${handledAttentionItems.length})`}</button>{!selectedAttentionItem && <><button type="button" className="secondary" onClick={() => setImportOpen(true)} disabled={!active.trip_id} title={!active.trip_id ? "确认创建这段旅行后即可导入攻略" : undefined}>从攻略导入</button>{!active.trip_id && <small>确认创建这段旅行后即可导入攻略。</small>}</>}</div>
            </section> : null}
            {active?.draft?.warnings.filter((warning) => !/(尚未明确安排(午餐|晚餐)|有约 \d+ 小时空档|尚无已选安排|尚未添加住宿地点|尚未设置出发地|安排跨度较长，地点间未检测到)/.test(warning)).map((warning) => <p className="advisor-live-warning" key={warning}>{warning}</p>)}
          </section>
        </div>
      </div>

      </>}

      {(active?.draft || active?.confirmed_trip_id) && <footer className="advisor-confirm-bar">
        <div><strong>{active?.status === "CONFIRMED" ? "行程已更新" : "确认后直接更新正式行程"}</strong><p>请先检查地点、时间和变更摘要；确认后会创建或更新正式计划。</p></div>
        {active?.draft && draftIsStale && <span role="status" className="advisor-live-error">旅行条件或正式行程已变化，请重新检查这份调整预览</span>}
        {active?.status === "CONFIRMED" && active.confirmed_trip_id ? <Link className="primary" href={`/trips/${active.confirmed_trip_id}/plan`}>查看已更新行程</Link> : <button type="button" className="primary" onClick={() => void confirmDraft()} disabled={!active?.draft || draftIsStale || busy}>{busy ? "正在更新行程…" : "确认并应用到行程"}</button>}
      </footer>}
      {importOpen && active?.trip_id && <TripImportTool tripId={active.trip_id} onClose={closeAdvisorImport} onImported={() => { setImportOpen(false); void refreshAfterGuideImport(); }} />}
      {whatIf && <AdvisorWhatIf tripId={whatIf.tripId} initialEventType={whatIf.eventType} prefill={whatIf.prefill} onClose={() => setWhatIf(null)} onApplied={() => void refreshAfterWhatIfApplied()} />}
    </main>
  );
}
