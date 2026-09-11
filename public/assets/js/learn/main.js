// My Courses: the student's course list and the lesson player with saved progress.
import { api } from "../lib/api.js";
import { openAuth } from "../lib/auth.js";
import { $, busy, html, on, render, toast } from "../lib/dom.js";
import { bytes, clock, dateOnly, durationText, firstName, sessionLabel } from "../lib/format.js";
import { boot, getUser, onUserChange, signOut } from "../lib/session.js";

const root = $("#learn-root");
const accountSlot = $("#learn-account");
const courseMatch = location.pathname.match(/^\/my-courses\/(\d+)/);
const SAVE_EVERY_MS = 15000;

let course = null;
let lessonId = null;
let video = null;
let saveTimer = null;
let lastSaved = 0;

function paintAccount(user) {
  render(accountSlot, user
    ? html`<span class="text-sm text-charcoal-light hidden sm:inline">${firstName(user)}</span>
        <button type="button" class="btn btn-ghost btn-sm" data-signout><i data-lucide="log-out" class="w-4 h-4"></i>Sign out</button>`
    : html`<button type="button" class="btn btn-outline btn-sm" data-signin>Sign in</button>`);
}

function signedOut() {
  render(root, html`
    <div class="max-w-md mx-auto text-center py-14">
      <div class="mx-auto w-14 h-14 rounded-full bg-terracotta-light text-terracotta grid place-items-center mb-4"><i data-lucide="graduation-cap" class="w-6 h-6"></i></div>
      <h1 class="font-serif text-3xl font-bold">Sign in to open your courses</h1>
      <p class="text-charcoal-light mt-3 leading-relaxed">Use the email you checked out with. Haven't set a password? Get a one-time code by email instead.</p>
      <div class="flex flex-wrap justify-center gap-2 mt-6">
        <button type="button" class="btn btn-primary" data-code>Email me a code</button>
        <button type="button" class="btn btn-outline" data-signin>Sign in with a password</button>
      </div>
    </div>`);
}

// ------------------------------------------------------------------ course list

async function showList() {
  document.title = "My courses · Rangdhaara Art Academy";
  let data;
  try {
    data = await api("/me/courses");
  } catch (err) {
    if (err.status === 401) return signedOut();
    render(root, html`<p class="form-error">${err.message}</p>`);
    return;
  }
  const { courses, workshops } = data;
  render(root, html`
    <h1 class="font-serif text-3xl sm:text-4xl font-bold">My courses</h1>
    ${!courses.length && !workshops.length ? html`
      <div class="rounded-3xl border border-dashed border-terracotta/40 bg-terracotta-light/50 p-10 text-center mt-8">
        <p class="font-serif text-xl font-bold">You haven't joined a course yet</p>
        <p class="text-charcoal-light mt-2">Bought one as a guest? Make sure you're signed in with the email you used at checkout.</p>
        <a href="/#academy" class="btn btn-primary mt-5">Explore the Academy</a>
      </div>` : ""}
    ${courses.length ? html`<div class="grid sm:grid-cols-2 lg:grid-cols-3 gap-6 mt-8">${courses.map((c) => html`
      <article class="art-card rounded-2xl overflow-hidden flex flex-col ${c.active ? "" : "opacity-70"}">
        <img src="${c.image || "/assets/images/profile_avatar.jpg"}" alt="" class="aspect-[16/10] w-full object-cover bg-sand">
        <div class="p-5 flex-1 flex flex-col gap-3">
          <div><p class="eyebrow">${c.level}</p><h2 class="font-serif text-xl font-bold leading-snug mt-1">${c.title}</h2></div>
          ${c.active && c.progress ? html`
            <div>
              <span class="progress" role="progressbar" aria-label="Course progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${c.progress.percent}"><span style="width:${c.progress.percent}%"></span></span>
              <p class="text-xs text-charcoal-light mt-1.5">${c.progress.completed} of ${c.progress.total} lessons complete</p>
            </div>
            <div class="mt-auto flex flex-wrap gap-2">
              <a class="btn btn-primary btn-sm" href="/my-courses/${c.course_id}"><i data-lucide="play" class="w-4 h-4"></i>${c.progress.completed === 0 ? "Start" : c.progress.is_complete ? "Watch again" : "Continue"}</a>
              ${c.certificate_available ? html`<a class="btn btn-outline btn-sm" href="/api/v1/courses/${c.course_id}/certificate"><i data-lucide="award" class="w-4 h-4"></i>Certificate</a>` : ""}
            </div>`
            : html`<p class="text-sm text-charcoal-light mt-auto">Your access to this course has ended.</p>`}
        </div>
      </article>`)}</div>` : ""}
    ${workshops.length ? html`
      <h2 class="font-serif text-2xl font-bold mt-12 mb-4">Live workshops</h2>
      <div class="grid md:grid-cols-2 gap-4">${workshops.map((w) => html`
        <article class="rounded-2xl border border-sand bg-white p-5">
          <p class="font-semibold">${w.title}</p>
          <p class="text-sm text-charcoal-light mt-1 flex items-center gap-1.5"><i data-lucide="calendar" class="w-4 h-4"></i>${sessionLabel(w.starts_at)} · ${w.duration_min} min · ${w.seats} seat${w.seats === 1 ? "" : "s"}</p>
          ${w.notes ? html`<p class="text-sm mt-2">${w.notes}</p>` : ""}
          ${w.meeting_url ? html`<a class="btn btn-primary btn-sm mt-4" href="${w.meeting_url}" target="_blank" rel="noopener"><i data-lucide="video" class="w-4 h-4"></i>Join the session</a>`
            : html`<p class="hint mt-3">${w.ended ? "This session has ended." : w.link_note}</p>`}
        </article>`)}</div>` : ""}`);
}

// ------------------------------------------------------------------ player

const allLessons = () => course.modules.flatMap((m) => m.lessons.map((l) => ({ ...l, module: m.title })));

async function showCourse(id) {
  try {
    course = await api(`/courses/${id}/learn`);
  } catch (err) {
    if (err.status === 401) return signedOut();
    render(root, html`
      <div class="max-w-lg mx-auto text-center py-14">
        <h1 class="font-serif text-3xl font-bold">${err.status === 403 ? "This course isn't on your account" : "We couldn't open this course"}</h1>
        <p class="text-charcoal-light mt-3">${err.message}</p>
        <div class="flex justify-center gap-2 mt-6"><a class="btn btn-primary" href="/my-courses">My courses</a><a class="btn btn-outline" href="/#academy">The Academy</a></div>
      </div>`);
    return;
  }
  document.title = `${course.course.title} · Rangdhaara Art Academy`;
  const lessons = allLessons();
  if (!lessons.length) {
    render(root, html`<h1 class="font-serif text-3xl font-bold">${course.course.title}</h1><p class="text-charcoal-light mt-3">Lessons for this course haven't been published yet.</p>`);
    return;
  }
  const fromHash = Number((location.hash.match(/lesson-(\d+)/) || [])[1]);
  lessonId = lessons.some((l) => l.id === fromHash) ? fromHash : (course.summary && course.summary.resume_lesson_id) || lessons[0].id;
  paintCourse();
  selectLesson(lessonId);
}

function paintCourse() {
  render(root, html`
    ${course.staff_preview ? html`<p class="dev-note mb-5"><i data-lucide="eye" class="w-4 h-4 shrink-0"></i>Staff preview — you can watch every lesson, but progress isn't saved for your account.</p>` : ""}
    <div class="grid lg:grid-cols-[minmax(0,1fr)_22rem] gap-6 items-start">
      <section data-stage></section>
      <aside class="rounded-2xl border border-sand bg-white lg:sticky lg:top-24 max-h-none lg:max-h-[calc(100vh-7.5rem)] flex flex-col" data-outline></aside>
    </div>`);
  paintOutline();
}

function paintOutline() {
  const slot = root.querySelector("[data-outline]");
  if (!slot) return;
  const s = course.summary;
  render(slot, html`
    <div class="p-5 border-b border-sand">
      <p class="eyebrow">${course.course.level}</p>
      <h1 class="font-serif text-xl font-bold leading-snug mt-1">${course.course.title}</h1>
      ${s ? html`
        <span class="progress mt-4" role="progressbar" aria-label="Course progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${s.percent}"><span style="width:${s.percent}%"></span></span>
        <p class="text-xs text-charcoal-light mt-1.5">${s.completed} of ${s.total} lessons complete</p>` : ""}
      ${course.course.certificate_enabled && s ? (course.certificate_available
        ? html`<a class="btn btn-primary btn-sm w-full mt-4" href="/api/v1/courses/${course.course.id}/certificate"><i data-lucide="award" class="w-4 h-4"></i>Download certificate</a>`
        : html`<p class="hint mt-3 flex items-center gap-1.5"><i data-lucide="award" class="w-3.5 h-3.5"></i>Finish every lesson to unlock your certificate.</p>`) : ""}
    </div>
    <nav class="overflow-y-auto p-2" aria-label="Lessons">
      ${course.modules.map((m) => html`
        <div class="py-2">
          <p class="px-3 py-1.5 text-xs font-bold uppercase tracking-wider text-charcoal-light">${m.title}</p>
          <ul>${m.lessons.map((l) => html`
            <li><button type="button" data-lesson="${l.id}" aria-current="${l.id === lessonId ? "true" : "false"}"
              class="w-full text-left flex items-start gap-3 px-3 py-2.5 rounded-xl text-sm ${l.id === lessonId ? "bg-terracotta-light text-charcoal font-semibold" : "hover:bg-cream"}">
              <i data-lucide="${l.completed ? "check-circle-2" : l.has_video ? "play-circle" : "file-text"}" class="w-4 h-4 mt-0.5 shrink-0 ${l.completed ? "text-emerald-700" : "text-charcoal-light"}"></i>
              <span class="flex-1">${l.title}</span>
              ${l.duration_s ? html`<span class="text-xs text-charcoal-light tabular-nums">${clock(l.duration_s)}</span>` : ""}
            </button></li>`)}
          </ul>
        </div>`)}
    </nav>`);
}

async function selectLesson(id) {
  await saveProgress();
  lessonId = id;
  history.replaceState(null, "", `#lesson-${id}`);
  paintOutline();
  const lessons = allLessons();
  const index = lessons.findIndex((l) => l.id === id);
  const lesson = lessons[index];
  const prev = lessons[index - 1];
  const next = lessons[index + 1];
  const resources = course.resources.filter((r) => r.lesson_id === id || r.lesson_id === null);
  const stage = root.querySelector("[data-stage]");
  render(stage, html`
    <div class="aspect-video rounded-2xl overflow-hidden bg-charcoal grid place-items-center text-white" data-player>
      ${lesson.has_video ? html`<span class="spinner" aria-label="Loading video"></span>` : html`<span class="text-center px-6"><i data-lucide="file-text" class="w-8 h-8 mx-auto opacity-70"></i><span class="block mt-2 text-sm opacity-80">This lesson is a reading lesson</span></span>`}
    </div>
    <div class="mt-5 flex flex-wrap items-start justify-between gap-4">
      <div class="min-w-0">
        <p class="eyebrow">${lesson.module}</p>
        <h2 class="font-serif text-2xl sm:text-3xl font-bold leading-tight mt-1">${lesson.title}</h2>
      </div>
      <div class="flex gap-2">
        <button type="button" class="btn btn-outline btn-sm" data-lesson="${prev ? prev.id : ""}" ${prev ? "" : "disabled"}><i data-lucide="chevron-left" class="w-4 h-4"></i>Previous</button>
        <button type="button" class="btn btn-primary btn-sm" data-lesson="${next ? next.id : ""}" ${next ? "" : "disabled"}>Next<i data-lucide="chevron-right" class="w-4 h-4"></i></button>
      </div>
    </div>
    ${lesson.description ? html`<p class="text-charcoal-light leading-relaxed mt-4 max-w-3xl whitespace-pre-line">${lesson.description}</p>` : ""}
    ${!lesson.has_video && !course.staff_preview ? html`<button type="button" class="btn ${lesson.completed ? "btn-outline" : "btn-primary"} mt-5" data-complete ${lesson.completed ? "disabled" : ""}>
      <i data-lucide="check" class="w-4 h-4"></i>${lesson.completed ? "Completed" : "Mark as complete"}</button>` : ""}
    ${resources.length ? html`
      <div class="mt-8">
        <h3 class="subhead mb-3">Downloads</h3>
        <ul class="grid sm:grid-cols-2 gap-2">${resources.map((r) => html`
          <li><a class="choice hover:border-terracotta" href="/api/v1/resources/${r.id}/download">
            <i data-lucide="download" class="w-4 h-4 text-terracotta shrink-0 mt-0.5"></i>
            <span><span class="block font-semibold text-charcoal">${r.title}</span><span class="text-xs text-charcoal-light">${r.filename} · ${bytes(r.size_bytes)}</span></span>
          </a></li>`)}
        </ul>
      </div>` : ""}`);

  if (lesson.has_video) {
    try {
      const playback = await api(`/lessons/${id}/playback`);
      if (lessonId !== id) return;
      mountVideo(lesson, playback.url);
    } catch (err) {
      render(stage.querySelector("[data-player]"), html`<p class="px-6 text-center text-sm">${err.message}</p>`);
    }
  }
}

function mountVideo(lesson, url) {
  const slot = root.querySelector("[data-player]");
  slot.innerHTML = "";
  video = document.createElement("video");
  video.src = url;
  video.controls = true;
  video.playsInline = true;
  video.preload = "metadata";
  video.className = "w-full h-full bg-black";
  video.setAttribute("controlsList", "nodownload");
  video.addEventListener("loadedmetadata", () => {
    const resumeAt = lesson.position_s || 0;
    if (resumeAt > 5 && resumeAt < video.duration - 5) video.currentTime = resumeAt;
  }, { once: true });
  video.addEventListener("pause", () => saveProgress());
  video.addEventListener("ended", () => saveProgress(true));
  slot.appendChild(video);
  clearInterval(saveTimer);
  saveTimer = setInterval(() => { if (video && !video.paused) saveProgress(); }, SAVE_EVERY_MS);
}

async function saveProgress(ended = false, { keepalive = false } = {}) {
  if (!course || course.staff_preview || !video || !lessonId || !Number.isFinite(video.duration)) return;
  const now = Date.now();
  if (!ended && !keepalive && now - lastSaved < 3000) return;
  lastSaved = now;
  const body = { position_s: ended ? video.duration : video.currentTime, duration_s: video.duration };
  const id = lessonId;
  try {
    if (keepalive) {
      fetch(`/api/v1/lessons/${id}/progress`, {
        method: "POST", keepalive: true, credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-Requested-With": "rangdhaara" }, body: JSON.stringify(body),
      });
      return;
    }
    const res = await api(`/lessons/${id}/progress`, { method: "POST", body });
    if (res.recorded) applyProgress(id, res);
  } catch { /* the next save will retry */ }
}

function applyProgress(id, res) {
  const wasComplete = course.summary && course.summary.is_complete;
  course.modules.forEach((m) => m.lessons.forEach((l) => {
    if (l.id === id) {
      l.completed = res.progress.completed;
      l.position_s = res.progress.last_position_s;
    }
  }));
  course.summary = res.summary;
  course.certificate_available = course.course.certificate_enabled && res.summary.is_complete;
  paintOutline();
  if (!wasComplete && res.summary.is_complete) toast("Course complete — your certificate is ready.", "success");
}

// ------------------------------------------------------------------ wiring

on(document, "click", "[data-lesson]", (e, el) => {
  const id = Number(el.dataset.lesson);
  if (id) selectLesson(id);
});

on(document, "click", "[data-complete]", async (e, el) => {
  busy(el, true, "Saving…");
  try {
    const res = await api(`/lessons/${lessonId}/progress`, { method: "POST", body: { position_s: 0, completed: true } });
    if (res.recorded) applyProgress(lessonId, res);
    selectLesson(lessonId);
  } catch (err) {
    busy(el, false);
    toast(err.message, "error");
  }
});

on(document, "click", "[data-signin]", () => openAuth({ reason: "Sign in with the email you used at checkout.", onDone: route }));
on(document, "click", "[data-code]", () => openAuth({ mode: "code", onDone: route }));
on(document, "click", "[data-signout]", async () => {
  await saveProgress();
  await signOut();
  location.href = "/";
});

window.addEventListener("pagehide", () => saveProgress(false, { keepalive: true }));

function route() {
  if (!getUser()) return signedOut();
  return courseMatch ? showCourse(Number(courseMatch[1])) : showList();
}

(async () => {
  try { await boot(); } catch (err) { toast(err.message, "error"); }
  paintAccount(getUser());
  onUserChange(paintAccount);
  route();
})();
