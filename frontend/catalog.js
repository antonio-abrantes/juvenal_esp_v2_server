(() => {
  const byId = (id) => document.getElementById(id);
  const dialog = byId("catalog-dialog");
  const form = byId("catalog-form");
  const status = byId("catalog-status");
  let snapshot = null;
  let editingId = null;
  let pending = false;

  const messages = {
    catalog_admin_credentials_required: "Configure PANEL_USERNAME e PANEL_PASSWORD no .env e reinicie para administrar.",
    catalog_unauthorized: "Autentique-se no painel para administrar o catálogo.",
    catalog_origin_rejected: "Abra o painel no próprio endereço do servidor para salvar.",
    catalog_revision_conflict: "Outra edição alterou o catálogo. Sua edição foi mantida; recarregue e revise antes de salvar.",
    catalog_size_limit: "O catálogo excede o tamanho permitido.",
    catalog_invalid: "O arquivo está inválido. Corrija-o ou salve um catálogo revisado.",
    catalog_entity_allowlist: "Informe uma ou mais entidades válidas do domínio da ação.",
    catalog_permissions_invalid: "Revise as identidades permitidas.",
    catalog_numeric_schema: "Revise os limites dos parâmetros numéricos.",
    catalog_parameter_range: "Os limites numéricos estão fora da faixa permitida.",
    catalog_duplicate_id: "Já existe uma automação com esse ID.",
    catalog_action_invalid: "Escolha uma ação cadastrada.",
    catalog_text_invalid: "Revise os textos; não inclua URLs, credenciais ou quebras de linha nos campos curtos."
  };

  function show(message, error = false) {
    status.textContent = message;
    status.classList.toggle("catalog-error", error);
  }

  function lock(value) {
    pending = value;
    dialog.querySelectorAll("button, input, select, textarea").forEach((element) => {
      if (element.id !== "catalog-close") element.disabled = value;
    });
    if (!value) {
      byId("catalog-new").disabled = !snapshot?.revision;
      byId("automation-id").disabled = editingId !== null;
      dialog.querySelectorAll("[data-entity-required]").forEach((element) => {
        element.disabled = true;
      });
    }
  }

  async function request(method = "GET", automations = null) {
    const options = { method, credentials: "same-origin", cache: "no-store" };
    if (method === "PUT") {
      options.headers = { "Content-Type": "application/json", "X-Juvenal-Catalog": "1" };
      options.body = JSON.stringify({ revision: snapshot.revision, automations });
    }
    const response = await fetch("/api/automations", options);
    const result = await response.json();
    if (!response.ok) throw new Error(messages[result.error] || "Não foi possível salvar ou carregar. Revise os campos e a configuração.");
    return result;
  }

  function render() {
    const list = byId("catalog-list");
    list.replaceChildren();
    for (const entry of snapshot.automations) {
      const row = document.createElement("li");
      const label = document.createElement("span");
      label.textContent = entry.title + (entry.enabled ? " · habilitada" : " · desabilitada");
      const edit = document.createElement("button");
      edit.type = "button";
      edit.textContent = "Editar";
      edit.onclick = () => editEntry(entry);
      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.textContent = entry.enabled ? "Desabilitar" : "Habilitar";
      toggle.onclick = () => save(snapshot.automations.map((item) =>
        item.id === entry.id ? { ...item, enabled: !item.enabled } : item));
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "Remover";
      remove.onclick = () => save(snapshot.automations.filter((item) => item.id !== entry.id));
      row.append(label, edit, toggle, remove);
      list.appendChild(row);
    }
    const actionSelect = byId("automation-action");
    actionSelect.replaceChildren();
    for (const action of Object.keys(snapshot.actions)) {
      const option = document.createElement("option");
      option.value = action;
      option.textContent = action;
      actionSelect.appendChild(option);
    }
    const reason = snapshot.capability.reason;
    const capability = snapshot.capability.available ? "Execução configurada." :
      reason === "catalog_has_no_enabled_actions" ? "Nenhuma ação habilitada." :
      reason === "automation_contract_unconfirmed" ? "Contrato do workflow ainda não confirmado." :
      reason === "provider_model_tools_unconfirmed" ? "Suporte do provedor/modelo ainda não confirmado." :
      "Execução indisponível ou desativada na configuração.";
    show(snapshot.valid ? capability : messages.catalog_invalid, !snapshot.valid);
  }

  async function reload() {
    if (pending) return;
    lock(true);
    try {
      snapshot = await request();
      form.classList.add("hidden");
      editingId = null;
      render();
    } catch (error) {
      show(error.message, true);
    } finally {
      lock(false);
    }
  }

  async function save(automations) {
    if (pending || !snapshot?.revision) return;
    lock(true);
    try {
      snapshot = await request("PUT", automations);
      form.classList.add("hidden");
      editingId = null;
      render();
      show("Catálogo salvo. A revisão passa a valer na próxima consulta ou execução.");
    } catch (error) {
      show(error.message, true);
    } finally {
      lock(false);
    }
  }

  function numericInput(labelText, value, schema, field) {
    const label = document.createElement("label");
    label.textContent = labelText;
    const input = document.createElement("input");
    input.type = "number";
    input.step = schema.type === "integer" ? "1" : "any";
    input.min = String(schema.minimum);
    input.max = String(schema.maximum);
    input.value = String(value);
    input.dataset.field = field;
    label.appendChild(input);
    return label;
  }

  function parameters(existing = {}) {
    const fieldset = byId("automation-parameters");
    fieldset.replaceChildren();
    const legend = document.createElement("legend");
    legend.textContent = "Parâmetros permitidos";
    fieldset.appendChild(legend);
    const action = snapshot.actions[byId("automation-action").value];
    for (const [name, schema] of Object.entries(action.parameters)) {
      const row = document.createElement("div");
      row.className = "catalog-parameter";
      row.dataset.parameter = name;
      const label = document.createElement("label");
      const included = document.createElement("input");
      included.type = "checkbox";
      included.dataset.field = "included";
      included.checked = name === "entity_id" || Boolean(existing[name]);
      if (name === "entity_id") {
        included.disabled = true;
        included.dataset.entityRequired = "true";
      }
      label.append(included, document.createTextNode(" " + name));
      row.appendChild(label);
      if (name === "entity_id") {
        const allowedLabel = document.createElement("label");
        allowedLabel.textContent = "Entidades permitidas, uma por linha";
        const input = document.createElement("textarea");
        input.rows = 2;
        input.maxLength = 4096;
        input.required = true;
        input.dataset.field = "enum";
        input.value = (existing[name]?.enum || []).join("\n");
        input.placeholder = action.domain + ".nome_da_entidade";
        allowedLabel.appendChild(input);
        row.appendChild(allowedLabel);
      } else {
        row.append(numericInput("Mínimo", existing[name]?.minimum ?? schema.minimum, schema, "minimum"),
          numericInput("Máximo", existing[name]?.maximum ?? schema.maximum, schema, "maximum"));
        const requiredLabel = document.createElement("label");
        const required = document.createElement("input");
        required.type = "checkbox";
        required.checked = existing[name]?.required || false;
        required.dataset.field = "required";
        requiredLabel.append(required, document.createTextNode(" Obrigatório quando esta automação é escolhida"));
        row.appendChild(requiredLabel);
      }
      fieldset.appendChild(row);
    }
  }

  function editEntry(entry = null) {
    if (pending || !snapshot) return;
    editingId = entry?.id ?? null;
    form.reset();
    byId("automation-id").value = entry?.id || "";
    byId("automation-id").disabled = editingId !== null;
    byId("automation-title").value = entry?.title || "";
    byId("automation-description").value = entry?.description || "";
    byId("automation-enabled").checked = entry?.enabled || false;
    byId("automation-action").value = entry?.action || Object.keys(snapshot.actions)[0];
    byId("automation-examples").value = (entry?.examples || []).join("\n");
    byId("automation-permissions").value = (entry?.permissions || ["local", "web:operator"]).join(", ");
    byId("automation-confirmation").value = entry?.confirmation || "always";
    byId("automation-result-hint").value = entry?.result_hint || "";
    parameters(entry?.parameters || {});
    form.classList.remove("hidden");
    byId("automation-title").focus();
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (pending) return;
    const actionName = byId("automation-action").value;
    const schemas = snapshot.actions[actionName].parameters;
    const parameterDefinitions = {};
    for (const row of byId("automation-parameters").querySelectorAll("[data-parameter]")) {
      const field = (name) => row.querySelector('[data-field="' + name + '"]');
      if (!field("included").checked) continue;
      const name = row.dataset.parameter;
      parameterDefinitions[name] = name === "entity_id" ?
        { type: "string", required: true, enum: field("enum").value.split("\n").map((s) => s.trim()).filter(Boolean) } :
        { type: schemas[name].type, required: field("required").checked,
          minimum: Number(field("minimum").value), maximum: Number(field("maximum").value) };
    }
    const entry = {
      id: byId("automation-id").value.trim(), title: byId("automation-title").value.trim(),
      description: byId("automation-description").value.trim(),
      enabled: byId("automation-enabled").checked, action: actionName,
      parameters: parameterDefinitions,
      examples: byId("automation-examples").value.split("\n").map((s) => s.trim()).filter(Boolean),
      permissions: byId("automation-permissions").value.split(",").map((s) => s.trim()).filter(Boolean),
      confirmation: byId("automation-confirmation").value,
      result_hint: byId("automation-result-hint").value.trim()
    };
    if (editingId === null && snapshot.automations.some((item) => item.id === entry.id)) {
      show(messages.catalog_duplicate_id, true);
      return;
    }
    save(editingId === null ? [...snapshot.automations, entry] :
      snapshot.automations.map((item) => item.id === editingId ? entry : item));
  });
  byId("catalog-open").onclick = () => { dialog.showModal(); reload(); };
  byId("catalog-close").onclick = () => dialog.close();
  byId("catalog-reload").onclick = reload;
  byId("catalog-new").onclick = () => editEntry();
  byId("catalog-cancel").onclick = () => { form.classList.add("hidden"); editingId = null; };
  byId("automation-action").onchange = () => parameters();
})();
