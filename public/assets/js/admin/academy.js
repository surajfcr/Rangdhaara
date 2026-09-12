// Academy: build courses (modules, lessons, videos, downloads) and schedule live workshops.
import { api, upload } from "../lib/api.js";
import { busy, html, render, toast } from "../lib/dom.js";
import { bytes, clock, dateOnly, dateTime, rupees, sessionLabel } from "../lib/format.js";
import { back, emptyRow, field, formDialog, pageHead, removeProductDialog } from "./ui.js";

const toPaise = (value) => Math.round(parseFloat(String(value).replace(/[₹,\s]/g, "")) * 100);
const liveChip = (active) => (active ? html`<span class="chip chip-ok">Live</span>` : html`<span class="chip chip-muted">Draft</span>`);

export default async function academy(el, ctx) {
  if (ctx.route === "workshops") return workshops(el, ctx);
  const [id] = ctx.params;
  return id ? course(el, ctx, Number(id)) : courses(el, ctx);
}

// ------------------------------------------------------------------ course list

async function courses(el, ctx) {
  const owner = ctx.user.role === "admin";
  let rows;
  let archived;
  const load = async () => {
    [{ courses: rows }, { products: archived }] = await Promise.all([
      api("/admin/courses"),
      api("/admin/products", { query: { kind: "course", archived: "true" } }),
    ]);
  };
  await load();
  if (!ctx.isCurrent()) return;

  const paint = () => render(el, html`
    ${pageHead("Courses", "Create masterclasses: add lessons and videos, set a price, then publish.",
      owner ? html`<button type="button" class="btn btn-primary btn-sm" data-new-course><i data-lucide="plus" class="w-4 h-4"></i>Create a course</button>` : "")}
    ${owner && !rows.length ? html`<div class="panel panel-body mb-4 text-sm">Start with <strong>Create a course</strong>. You'll then add modules and lessons, upload a video for each lesson, and publish when it's ready.</div>` : ""}
    <div class="panel overflow-hidden"><div class="table-wrap"><table class="data-table">
      <thead><tr><th>Course</th><th>Status</th><th class="num">Lessons</th><th class="num">Length</th><th class="num">Students</th><th class="num">Price</th>${owner ? html`<th></th>` : ""}</tr></thead>
      <tbody>${rows.length ? rows.map((c) => html`
        <tr data-href="#academy/${c.course_id}">
          <td><div class="flex items-center gap-3"><img src="${c.image || "/assets/images/profile_avatar.jpg"}" alt="" class="w-14 h-10 rounded-lg object-cover bg-sand">
            <span><span class="block font-semibold">${c.title}</span><span class="block text-xs text-charcoal-light">${c.course ? c.course.level : ""}</span></span></div></td>
          <td><div class="flex flex-col gap-1">${liveChip(c.is_active)}${!c.is_active && c.publish_problems.length ? html`<span class="text-xs text-charcoal-light">${c.publish_problems.length} to fix</span>` : ""}</div></td>
          <td class="num">${c.course ? c.course.lesson_count : 0}</td>
          <td class="num">${c.course && c.course.total_minutes ? `${c.course.total_minutes} min` : "—"}</td>
          <td class="num">${c.students}</td>
          <td class="num">${c.price_paise ? rupees(c.price_paise) : html`<span class="text-red-700">Not set</span>`}</td>
          ${owner ? html`<td class="text-right whitespace-nowrap"><a class="btn btn-ghost btn-sm" href="#academy/${c.course_id}">Edit</a>
            <button type="button" class="icon-btn text-red-700" data-remove-course="${c.id}" aria-label="Remove ${c.title}" title="Remove"><i data-lucide="trash-2" class="w-4 h-4"></i></button></td>` : ""}
        </tr>`) : emptyRow(owner ? 7 : 6, "No courses yet.")}</tbody>
    </table></div></div>
    ${archived.length ? html`
      <h2 class="font-semibold mt-8 mb-3">Archived courses</h2>
      <div class="panel overflow-hidden"><ul class="divide-y divide-sand">${archived.map((c) => html`
        <li class="px-4 py-3 flex items-center justify-between gap-3 text-sm">
          <span><span class="block font-medium">${c.title}</span><span class="text-xs text-charcoal-light">Archived ${dateOnly(c.archived_at)} · students keep their access</span></span>
          ${owner ? html`<button type="button" class="btn btn-outline btn-sm" data-restore-course="${c.id}"><i data-lucide="archive-restore" class="w-4 h-4"></i>Restore</button>` : ""}
        </li>`)}</ul></div>` : ""}`);

  el.onclick = async (e) => {
    let b;
    if ((b = e.target.closest("[data-remove-course]"))) {
      const course = rows.find((c) => c.id === b.dataset.removeCourse);
      const result = await removeProductDialog(course, api);
      if (result) { toast(result.message, "success"); await load(); paint(); }
      return;
    }
    if ((b = e.target.closest("[data-restore-course]"))) {
      try {
        await api(`/admin/products/${b.dataset.restoreCourse}/restore`, { method: "POST" });
        toast("Course restored as a draft.", "success");
        await load();
        paint();
      } catch (err) { toast(err.message, "error"); }
      return;
    }
    if (!e.target.closest("[data-new-course]")) return;
    const created = await formDialog({
      title: "Create a course",
      description: "It's saved as a draft. Next you'll add lessons and videos, then publish it.",
      submitLabel: "Create course",
      wide: true,
      body: html`
        ${field("Course name", html`<input class="input" name="title" required autofocus placeholder="e.g. Palette Knife Florals for Beginners">`)}
        <div class="grid sm:grid-cols-2 gap-4">
          ${field("Price (₹)", html`<input class="input" name="price" inputmode="decimal" placeholder="e.g. 1499">`, "You can leave this blank and set it later.")}
          ${field("Level", html`<select class="input" name="level">${["Beginner", "Intermediate", "Advanced", "All levels"].map((lv) => html`<option>${lv}</option>`)}</select>`)}
        </div>
        ${field("Description", html`<textarea class="input" name="description" rows="3" placeholder="What students will make and learn"></textarea>`)}
        ${field("Instructor", html`<input class="input" name="instructor" value="Rangdhara Studio">`)}
        ${field("Cover photo", html`<input class="input !py-2" type="file" name="photo" accept="image/jpeg,image/png,image/webp">`, "Shown on the course card in the store.")}`,
      onSubmit: async (v, form) => {
        const product = await api("/admin/products", { method: "POST", body: {
          kind: "course", title: v.title, description: v.description, price_paise: v.price ? toPaise(v.price) || 0 : 0 } });
        const notes = [];
        try {
          await api(`/admin/courses/${product.course_id}`, { method: "PATCH", body: { level: v.level, instructor: v.instructor || "Rangdhara Studio" } });
        } catch (err) { notes.push(err.message); }
        const photo = form.photo.files[0];
        if (photo) {
          const data = new FormData();
          data.append("file", photo);
          data.append("alt", v.title);
          try { await upload(`/admin/products/${product.id}/media`, data); } catch (err) { notes.push(`The cover photo didn't upload: ${err.message}`); }
        }
        return { product, notes };
      },
    });
    if (!created) return;
    toast(created.notes.length ? created.notes.join(" ") : "Course created. Now add a module and your first lesson.", created.notes.length ? "error" : "success");
    ctx.navigate(`#academy/${created.product.course_id}`);
  };
  paint();
}

// ------------------------------------------------------------------ course builder

function readDuration(file) {
  return new Promise((resolve) => {
    const probe = document.createElement("video");
    const url = URL.createObjectURL(file);
    const done = (seconds) => { URL.revokeObjectURL(url); resolve(Math.round(seconds || 0)); };
    probe.preload = "metadata";
    probe.onloadedmetadata = () => done(probe.duration);
    probe.onerror = () => done(0);
    setTimeout(() => done(0), 8000);
    probe.src = url;
  });
}

async function course(el, ctx, courseId) {
  const owner = ctx.user.role === "admin";
  let d = await api(`/admin/courses/${courseId}`);
  if (!ctx.isCurrent()) return;
  const uploads = new Map();

  function paint() {
    const p = d.product;
    const c = d.course;
    const variant = p.variants[0];
    render(el, html`
      ${back("#academy", "Courses")}
      <div class="flex flex-wrap items-start justify-between gap-4 mt-3 mb-6">
        <div>
          <h1 class="font-serif text-3xl font-bold leading-tight">${c.title}</h1>
          <div class="flex flex-wrap items-center gap-2 mt-2">${liveChip(p.is_active)}
            <span class="text-sm text-charcoal-light">${p.course ? `${p.course.lesson_count} lessons · ${p.course.total_minutes} min` : ""} · <a class="link" href="#students/${c.id}">${d.students} student${d.students === 1 ? "" : "s"}</a></span></div>
        </div>
        <div class="flex flex-wrap gap-2">
          <a class="btn btn-outline btn-sm" href="/my-courses/${c.id}" target="_blank" rel="noopener"><i data-lucide="eye" class="w-4 h-4"></i>Preview as a student</a>
          ${owner ? html`<button type="button" class="btn ${p.is_active ? "btn-outline" : "btn-primary"} btn-sm" data-publish="${!p.is_active}">${p.is_active ? "Unpublish" : "Publish"}</button>
            <button type="button" class="btn btn-ghost btn-sm text-red-700" data-remove-this-course><i data-lucide="trash-2" class="w-4 h-4"></i>Remove</button>` : ""}
        </div>
      </div>

      ${!p.is_active && p.publish_problems.length ? html`
        <div class="panel panel-body mb-4 bg-[#FFFBF3] border-amber-200">
          <p class="font-semibold text-amber-900">Before this course can go live</p>
          <ul class="tick-list mt-2">${p.publish_problems.map((x) => html`<li>${x}</li>`)}</ul>
        </div>` : ""}

      <div class="grid xl:grid-cols-[minmax(0,1fr)_24rem] gap-4 items-start">
        <div class="space-y-4">
          <section class="panel">
            <div class="panel-head"><h2 class="font-semibold">Lessons</h2><span class="text-xs text-charcoal-light">Students watch them in this order</span></div>
            ${d.modules.length ? d.modules.map((m, mi) => html`
              <div class="border-b border-sand last:border-0">
                <div class="flex flex-wrap items-center justify-between gap-2 px-4 py-3 bg-[#FBF8F4]">
                  <p class="font-semibold">${mi + 1}. ${m.title}</p>
                  ${owner ? html`<div class="flex items-center gap-1">
                    <button type="button" class="icon-btn !w-8 !h-8" data-module-move="${m.id}" data-dir="-1" aria-label="Move module up" ${mi === 0 ? "disabled" : ""}><i data-lucide="arrow-up" class="w-4 h-4"></i></button>
                    <button type="button" class="icon-btn !w-8 !h-8" data-module-move="${m.id}" data-dir="1" aria-label="Move module down" ${mi === d.modules.length - 1 ? "disabled" : ""}><i data-lucide="arrow-down" class="w-4 h-4"></i></button>
                    <button type="button" class="btn btn-ghost btn-sm" data-module-rename="${m.id}">Rename</button>
                    <button type="button" class="btn btn-ghost btn-sm text-red-700" data-module-delete="${m.id}">Delete</button>
                    <button type="button" class="btn btn-outline btn-sm" data-lesson-add="${m.id}"><i data-lucide="plus" class="w-4 h-4"></i>Lesson</button>
                  </div>` : ""}
                </div>
                ${m.lessons.length ? html`<ul class="divide-y divide-sand">${m.lessons.map((l, li) => {
                  const progress = uploads.get(l.id);
                  return html`<li class="px-4 py-3 flex flex-wrap items-center gap-3">
                    <span class="w-6 text-sm text-charcoal-light tabular-nums">${li + 1}</span>
                    <span class="flex-1 min-w-[12rem]">
                      <span class="block font-medium">${l.title}${l.is_preview ? html` <span class="chip chip-info ml-1">Free preview</span>` : ""}</span>
                      <span class="block text-xs ${l.has_video ? "text-charcoal-light" : "text-amber-800"}">${l.has_video ? `Video uploaded · ${clock(l.duration_s)} · ${bytes(l.video_size)}` : "No video yet"}</span>
                      ${progress !== undefined ? html`<span class="upload-bar block mt-2 max-w-xs" role="progressbar" aria-valuenow="${Math.round(progress * 100)}" aria-valuemin="0" aria-valuemax="100"><span style="width:${Math.round(progress * 100)}%"></span></span>` : ""}
                    </span>
                    ${owner ? html`<span class="flex flex-wrap items-center gap-1">
                      <label class="btn btn-outline btn-sm cursor-pointer ${progress !== undefined ? "opacity-50 pointer-events-none" : ""}"><i data-lucide="upload" class="w-4 h-4"></i>${l.has_video ? "Replace video" : "Upload video"}
                        <input type="file" accept="video/mp4,video/webm,video/quicktime,.m4v" class="sr-only" data-video="${l.id}"></label>
                      <button type="button" class="btn btn-ghost btn-sm" data-lesson-edit="${l.id}">Edit</button>
                      <button type="button" class="icon-btn !w-8 !h-8" data-lesson-move="${l.id}" data-module="${m.id}" data-dir="-1" aria-label="Move up" ${li === 0 ? "disabled" : ""}><i data-lucide="arrow-up" class="w-4 h-4"></i></button>
                      <button type="button" class="icon-btn !w-8 !h-8" data-lesson-move="${l.id}" data-module="${m.id}" data-dir="1" aria-label="Move down" ${li === m.lessons.length - 1 ? "disabled" : ""}><i data-lucide="arrow-down" class="w-4 h-4"></i></button>
                      <button type="button" class="icon-btn !w-8 !h-8 text-red-700" data-lesson-delete="${l.id}" aria-label="Delete lesson"><i data-lucide="trash-2" class="w-4 h-4"></i></button>
                    </span>` : ""}
                  </li>`;
                })}</ul>` : html`<p class="px-4 py-4 text-sm text-charcoal-light">No lessons in this module yet.</p>`}
              </div>`) : html`<p class="panel-body text-sm text-charcoal-light">Start by adding a module — a group of related lessons, like “Getting started”.</p>`}
            ${owner ? html`<form class="panel-body border-t border-sand flex gap-2" data-module-add>
              <label class="sr-only" for="new-module">New module name</label>
              <input id="new-module" class="input" name="title" placeholder="New module name" required>
              <button type="submit" class="btn btn-outline shrink-0">Add module</button>
            </form>` : ""}
          </section>

          <section class="panel">
            <div class="panel-head"><h2 class="font-semibold">Downloads</h2><span class="text-xs text-charcoal-light">PDF guides, supply lists, templates</span></div>
            ${d.resources.length ? html`<ul class="divide-y divide-sand">${d.resources.map((r) => html`
              <li class="px-4 py-3 flex items-center justify-between gap-3 text-sm">
                <span><span class="block font-medium">${r.title}</span><span class="text-xs text-charcoal-light">${r.filename} · ${bytes(r.size_bytes)}</span></span>
                ${owner ? html`<button type="button" class="btn btn-ghost btn-sm text-red-700" data-resource-delete="${r.id}">Delete</button>` : ""}
              </li>`)}</ul>` : html`<p class="panel-body text-sm text-charcoal-light">No downloads yet.</p>`}
            ${owner ? html`<form class="panel-body border-t border-sand grid sm:grid-cols-[1fr_1fr_auto] gap-2 items-end" data-resource-add>
              ${field("Title", html`<input class="input" name="title" placeholder="e.g. Supply checklist">`)}
              ${field("File (PDF, ZIP, image or text, up to 100 MB)", html`<input class="input !py-2" type="file" name="file" accept=".pdf,.zip,.png,.jpg,.jpeg,.txt" required>`)}
              <button type="submit" class="btn btn-outline">Upload</button>
            </form>` : ""}
          </section>
        </div>

        <form class="panel" data-course-form>
          <div class="panel-head"><h2 class="font-semibold">Course details</h2></div>
          <fieldset class="panel-body space-y-4" ${owner ? "" : "disabled"}>
            ${field("Name", html`<input class="input" name="title" value="${c.title}" required>`)}
            ${field("Price (₹)", html`<input class="input" name="price" inputmode="decimal" value="${variant && variant.price_paise ? variant.price_paise / 100 : ""}">`)}
            ${field("Description", html`<textarea class="input" name="description" rows="4">${p.description}</textarea>`)}
            <div class="grid grid-cols-2 gap-3">
              ${field("Level", html`<select class="input" name="level">${["Beginner", "Intermediate", "Advanced", "All levels"].map((lv) => html`<option ${lv === c.level ? "selected" : ""}>${lv}</option>`)}</select>`)}
              ${field("Access", html`<input class="input" type="number" min="1" name="access_days" value="${c.access_days || ""}" placeholder="Lifetime">`, "Days. Blank means lifetime.")}
            </div>
            ${field("Instructor", html`<input class="input" name="instructor" value="${c.instructor}">`)}
            ${field("What students will learn", html`<textarea class="input" name="outcomes" rows="4">${c.outcomes.join("\n")}</textarea>`, "One outcome per line.")}
            <label class="flex items-center gap-2.5 text-sm"><input type="checkbox" class="checkbox" name="certificate_enabled" ${c.certificate_enabled ? "checked" : ""}>Issue a certificate when every lesson is complete</label>
            <p class="text-sm"><a class="link" href="#catalogue/${p.id}">Manage the course photo</a></p>
          </fieldset>
          ${owner ? html`<div class="px-4 pb-4"><button type="submit" class="btn btn-primary w-full">Save details</button></div>` : ""}
        </form>
      </div>`);
  }

  const reload = async (message) => {
    d = await api(`/admin/courses/${courseId}`);
    if (message) toast(message, "success");
    paint();
  };
  const guard = async (fn, message) => {
    try {
      const result = await fn();
      if (result && result.course) {
        d = result;
        if (message) toast(message, "success");
        paint();
      } else {
        await reload(message);
      }
    } catch (err) {
      toast(err.data && err.data.problems ? `${err.message} ${err.data.problems.join(" ")}` : err.message, "error");
    }
  };
  const lessonById = (lid) => d.modules.flatMap((m) => m.lessons).find((l) => l.id === lid);

  el.onsubmit = async (e) => {
    e.preventDefault();
    const form = e.target;
    const button = form.querySelector('button[type="submit"]');
    busy(button, true, "Saving…");
    try {
      if (form.matches("[data-module-add]")) {
        d = await api(`/admin/courses/${courseId}/modules`, { method: "POST", body: { title: form.title.value } });
        paint();
      } else if (form.matches("[data-resource-add]")) {
        const data = new FormData();
        data.append("file", form.file.files[0]);
        data.append("title", form.title.value);
        d = await upload(`/admin/courses/${courseId}/resources`, data);
        toast("Download added.", "success");
        paint();
      } else if (form.matches("[data-course-form]")) {
        const v = Object.fromEntries(new FormData(form));
        await api(`/admin/products/${d.product.id}`, { method: "PATCH", body: { title: v.title, description: v.description } });
        if (v.price) await api(`/admin/variants/${d.product.variants[0].id}`, { method: "PATCH", body: { price_paise: toPaise(v.price) } });
        d = await api(`/admin/courses/${courseId}`, { method: "PATCH", body: {
          level: v.level, instructor: v.instructor, outcomes: String(v.outcomes || "").split("\n").map((x) => x.trim()).filter(Boolean),
          certificate_enabled: form.certificate_enabled.checked, access_days: v.access_days ? Number(v.access_days) : null } });
        toast("Course details saved.", "success");
        paint();
      }
    } catch (err) {
      busy(button, false);
      toast(err.message, "error");
    }
  };

  el.onchange = async (e) => {
    const input = e.target.closest("[data-video]");
    if (!input || !input.files.length) return;
    const lessonId = Number(input.dataset.video);
    const file = input.files[0];
    const duration = await readDuration(file);
    const data = new FormData();
    data.append("file", file);
    data.append("duration_s", String(duration));
    uploads.set(lessonId, 0);
    paint();
    let last = 0;
    try {
      d = await upload(`/admin/lessons/${lessonId}/video`, data, (fraction) => {
        uploads.set(lessonId, fraction);
        if (fraction - last > 0.04 || fraction === 1) { last = fraction; paint(); }
      });
      uploads.delete(lessonId);
      toast("Video uploaded.", "success");
      paint();
    } catch (err) {
      uploads.delete(lessonId);
      paint();
      toast(err.message, "error");
    }
  };

  el.onclick = async (e) => {
    const t = (sel) => e.target.closest(sel);
    let b;
    if (t("[data-remove-this-course]")) {
      const result = await removeProductDialog(d.product, api);
      if (!result) return;
      toast(result.message, "success");
      ctx.navigate("#academy"); // deleted, or archived (listed under Archived courses)
      return;
    }
    if ((b = t("[data-publish]"))) {
      return guard(async () => { await api(`/admin/products/${d.product.id}`, { method: "PATCH", body: { is_active: b.dataset.publish === "true" } }); },
        b.dataset.publish === "true" ? "Published — students can enrol now." : "Unpublished.");
    }
    if ((b = t("[data-module-move]"))) {
      const ids = d.modules.map((m) => m.id);
      const from = ids.indexOf(Number(b.dataset.moduleMove));
      const to = from + Number(b.dataset.dir);
      [ids[from], ids[to]] = [ids[to], ids[from]];
      return guard(() => api(`/admin/courses/${courseId}/modules/order`, { method: "POST", body: { ids } }));
    }
    if ((b = t("[data-lesson-move]"))) {
      const module = d.modules.find((m) => m.id === Number(b.dataset.module));
      const ids = module.lessons.map((l) => l.id);
      const from = ids.indexOf(Number(b.dataset.lessonMove));
      const to = from + Number(b.dataset.dir);
      [ids[from], ids[to]] = [ids[to], ids[from]];
      return guard(() => api(`/admin/modules/${module.id}/lessons/order`, { method: "POST", body: { ids } }));
    }
    if ((b = t("[data-module-rename]"))) {
      const module = d.modules.find((m) => m.id === Number(b.dataset.moduleRename));
      const result = await formDialog({ title: "Rename module", submitLabel: "Save", body: field("Module name", html`<input class="input" name="title" value="${module.title}" required autofocus>`),
        onSubmit: (v) => api(`/admin/modules/${module.id}`, { method: "PATCH", body: { title: v.title } }) });
      if (result) { d = result; paint(); }
      return;
    }
    if ((b = t("[data-module-delete]"))) {
      if (!confirm("Delete this module, its lessons and their videos? Students lose their progress on those lessons.")) return;
      return guard(() => api(`/admin/modules/${b.dataset.moduleDelete}`, { method: "DELETE" }), "Module deleted.");
    }
    if ((b = t("[data-lesson-add]"))) {
      const result = await formDialog({
        title: "Add a lesson",
        submitLabel: "Add lesson",
        body: html`${field("Lesson title", html`<input class="input" name="title" required autofocus>`)}
          ${field("Description", html`<textarea class="input" name="description" rows="3"></textarea>`, "Optional. Shown under the video.")}
          <label class="flex items-center gap-2.5 text-sm"><input type="checkbox" class="checkbox" name="is_preview">Free preview — anyone can watch it from the store</label>`,
        onSubmit: (v) => api(`/admin/modules/${b.dataset.lessonAdd}/lessons`, { method: "POST", body: { title: v.title, description: v.description, is_preview: v.is_preview } }),
      });
      if (result) { d = result; toast("Lesson added. Upload its video next.", "success"); paint(); }
      return;
    }
    if ((b = t("[data-lesson-edit]"))) {
      const lesson = lessonById(Number(b.dataset.lessonEdit));
      const result = await formDialog({
        title: "Edit lesson",
        submitLabel: "Save",
        body: html`${field("Lesson title", html`<input class="input" name="title" value="${lesson.title}" required autofocus>`)}
          ${field("Description", html`<textarea class="input" name="description" rows="4">${lesson.description}</textarea>`)}
          <label class="flex items-center gap-2.5 text-sm"><input type="checkbox" class="checkbox" name="is_preview" ${lesson.is_preview ? "checked" : ""}>Free preview — anyone can watch it from the store</label>
          ${lesson.has_video ? html`<label class="flex items-center gap-2.5 text-sm text-red-700"><input type="checkbox" class="checkbox" name="remove_video">Remove this lesson's video</label>` : ""}`,
        onSubmit: async (v) => {
          if (v.remove_video) await api(`/admin/lessons/${lesson.id}/video`, { method: "DELETE" });
          return api(`/admin/lessons/${lesson.id}`, { method: "PATCH", body: { title: v.title, description: v.description, is_preview: v.is_preview } });
        },
      });
      if (result) { d = result; paint(); }
      return;
    }
    if ((b = t("[data-lesson-delete]"))) {
      if (!confirm("Delete this lesson and its video?")) return;
      return guard(() => api(`/admin/lessons/${b.dataset.lessonDelete}`, { method: "DELETE" }), "Lesson deleted.");
    }
    if ((b = t("[data-resource-delete]"))) {
      if (!confirm("Delete this download?")) return;
      return guard(() => api(`/admin/resources/${b.dataset.resourceDelete}`, { method: "DELETE" }), "Download deleted.");
    }
  };

  paint();
}

// ------------------------------------------------------------------ workshops

async function workshops(el, ctx) {
  const owner = ctx.user.role === "admin";
  let data = await api("/admin/workshops");
  if (!ctx.isCurrent()) return;

  const sessionForm = (s = {}) => html`
    ${field("Starts (India time)", html`<input class="input" type="datetime-local" name="starts_at" required value="${s.local || ""}">`)}
    <div class="grid grid-cols-3 gap-3">
      ${field("Length (min)", html`<input class="input" type="number" min="15" name="duration_min" value="${s.duration_min || 120}" required>`)}
      ${field("Seats", html`<input class="input" type="number" min="1" name="seats_total" value="${s.seats_total || 12}" required>`)}
      ${s.local === undefined ? field("Price (₹)", html`<input class="input" name="price" inputmode="decimal" required>`) : html`<div></div>`}
    </div>
    ${field("Meeting link", html`<input class="input" type="url" name="meeting_url" placeholder="https://meet.google.com/…" value="${s.meeting_url || ""}">`, "Students see it in My Courses from 48 hours before the start.")}
    ${field("Notes for students", html`<textarea class="input" name="notes" rows="2" placeholder="e.g. Keep your kit and an apron ready">${s.notes || ""}</textarea>`)}`;

  const toLocalInput = (iso) => {
    const d = new Date(new Date(iso).getTime() + 330 * 60000);
    return d.toISOString().slice(0, 16);
  };

  function paint() {
    render(el, html`
      ${pageHead("Workshops", "Live online classes with limited seats. Each session has its own date, price and seat count.",
        owner ? html`<button type="button" class="btn btn-primary btn-sm" data-new-workshop><i data-lucide="plus" class="w-4 h-4"></i>New workshop</button>` : "")}
      <div class="space-y-4">${data.workshops.length ? data.workshops.map((w) => html`
        <section class="panel">
          <div class="panel-head flex-wrap">
            <div class="flex items-center gap-3 min-w-0">
              <img src="${w.image || "/assets/images/profile_avatar.jpg"}" alt="" class="w-12 h-12 rounded-lg object-cover bg-sand">
              <div class="min-w-0"><h2 class="font-semibold truncate">${w.title}</h2><div class="flex items-center gap-2 mt-1">${liveChip(w.is_active)}
                ${!w.is_active && w.publish_problems.length ? html`<span class="text-xs text-charcoal-light">${w.publish_problems.join(" ")}</span>` : ""}</div></div>
            </div>
            <div class="flex flex-wrap gap-2">
              <a class="btn btn-ghost btn-sm" href="#catalogue/${w.id}">Details & photos</a>
              ${owner ? html`<button type="button" class="icon-btn text-red-700" data-remove-workshop="${w.id}" aria-label="Remove ${w.title}" title="Remove"><i data-lucide="trash-2" class="w-4 h-4"></i></button>` : ""}
              ${owner ? html`<button type="button" class="btn btn-outline btn-sm" data-add-session="${w.id}"><i data-lucide="calendar-plus" class="w-4 h-4"></i>Add session</button>
                <button type="button" class="btn ${w.is_active ? "btn-outline" : "btn-primary"} btn-sm" data-publish="${w.id}" data-to="${!w.is_active}">${w.is_active ? "Unpublish" : "Publish"}</button>` : ""}
            </div>
          </div>
          <div class="table-wrap"><table class="data-table">
            <thead><tr><th>Session</th><th class="num">Booked</th><th class="num">Price</th><th>Meeting link</th><th></th></tr></thead>
            <tbody>${w.sessions.length ? w.sessions.map((s) => {
              const past = new Date(s.starts_at) < new Date();
              return html`<tr class="${past || !s.is_active ? "opacity-60" : ""}">
                <td><span class="block font-medium">${sessionLabel(s.starts_at)}</span><span class="text-xs text-charcoal-light">${s.duration_min} min${past ? " · finished" : ""}${s.is_active ? "" : " · hidden"}</span></td>
                <td class="num">${s.seats_taken} / ${s.seats_total}</td>
                <td class="num">${rupees(s.price_paise)}</td>
                <td>${s.meeting_url ? html`<a class="link text-sm" href="${s.meeting_url}" target="_blank" rel="noopener">Open</a>` : html`<span class="text-sm text-amber-800">Not added yet</span>`}</td>
                <td class="text-right whitespace-nowrap">
                  <button type="button" class="btn btn-ghost btn-sm" data-bookings="${s.variant_id}" data-label="${sessionLabel(s.starts_at)}">Bookings</button>
                  ${owner ? html`<button type="button" class="btn btn-ghost btn-sm" data-edit-session="${s.variant_id}">Edit</button>` : ""}
                </td>
              </tr>`;
            }) : emptyRow(5, "No sessions scheduled.")}</tbody>
          </table></div>
        </section>`) : html`<div class="panel panel-body text-sm text-charcoal-light">No workshops yet.</div>`}
      </div>`);
  }

  const findSession = (variantId) => data.workshops.flatMap((w) => w.sessions).find((s) => s.variant_id === variantId);

  el.onclick = async (e) => {
    let b;
    if ((b = e.target.closest("[data-remove-workshop]"))) {
      const workshop = data.workshops.find((w) => w.id === b.dataset.removeWorkshop);
      const result = await removeProductDialog(workshop, api);
      if (result) { toast(result.message, "success"); data = await api("/admin/workshops"); paint(); }
      return;
    }
    if ((b = e.target.closest("[data-new-workshop]"))) {
      const created = await formDialog({ title: "New workshop", description: "Add photos and at least one session, then publish.", submitLabel: "Create workshop",
        body: field("Workshop name", html`<input class="input" name="title" required autofocus>`),
        onSubmit: (v) => api("/admin/products", { method: "POST", body: { kind: "workshop", title: v.title } }) });
      if (created) { data = await api("/admin/workshops"); paint(); }
    } else if ((b = e.target.closest("[data-add-session]"))) {
      const result = await formDialog({ title: "Add a session", submitLabel: "Add session", body: sessionForm(),
        onSubmit: (v) => api(`/admin/products/${b.dataset.addSession}/sessions`, { method: "POST", body: {
          starts_at: v.starts_at, duration_min: Number(v.duration_min), seats_total: Number(v.seats_total), price_paise: toPaise(v.price), meeting_url: v.meeting_url, notes: v.notes } }) });
      if (result) { data = result; toast("Session added.", "success"); paint(); }
    } else if ((b = e.target.closest("[data-edit-session]"))) {
      const s = findSession(Number(b.dataset.editSession));
      const result = await formDialog({ title: "Edit session", submitLabel: "Save",
        body: html`${sessionForm({ ...s, local: toLocalInput(s.starts_at) })}
          <label class="flex items-center gap-2.5 text-sm"><input type="checkbox" class="checkbox" name="is_active" ${s.is_active ? "checked" : ""}>Open for booking</label>`,
        onSubmit: (v) => api(`/admin/sessions/${s.variant_id}`, { method: "PATCH", body: {
          starts_at: v.starts_at, duration_min: Number(v.duration_min), seats_total: Number(v.seats_total), meeting_url: v.meeting_url, notes: v.notes, is_active: v.is_active } }) });
      if (result) { data = result; toast("Session saved.", "success"); paint(); }
    } else if ((b = e.target.closest("[data-publish]"))) {
      busy(b, true, "Saving…");
      try {
        await api(`/admin/products/${b.dataset.publish}`, { method: "PATCH", body: { is_active: b.dataset.to === "true" } });
        data = await api("/admin/workshops");
        toast(b.dataset.to === "true" ? "Published." : "Unpublished.", "success");
        paint();
      } catch (err) {
        busy(b, false);
        toast(err.data && err.data.problems ? `${err.message} ${err.data.problems.join(" ")}` : err.message, "error");
      }
    } else if ((b = e.target.closest("[data-bookings]"))) {
      const { bookings } = await api(`/admin/sessions/${b.dataset.bookings}/bookings`);
      await formDialog({
        title: "Bookings",
        description: b.dataset.label,
        submitLabel: "Done",
        wide: true,
        body: bookings.length ? html`<div class="table-wrap border border-sand rounded-xl"><table class="data-table">
          <thead><tr><th>Student</th><th>Contact</th><th class="num">Seats</th><th>Booked</th></tr></thead>
          <tbody>${bookings.map((k) => html`<tr class="${k.status === "cancelled" ? "opacity-50" : ""}"><td>${k.full_name}${k.status === "cancelled" ? " (cancelled)" : ""}</td>
            <td><span class="block">${k.email}</span><span class="text-xs text-charcoal-light">${k.phone || ""}</span></td><td class="num">${k.seats}</td><td class="whitespace-nowrap">${dateTime(k.created_at)}</td></tr>`)}</tbody>
        </table></div>` : html`<p class="text-sm text-charcoal-light">Nobody has booked this session yet.</p>`,
      });
    }
  };

  paint();
}
