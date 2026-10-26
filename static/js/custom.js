/**
 * Custom application JavaScript
 * Handles CRUD modals, CSRF token management, and generic actions.
 */
document.addEventListener("DOMContentLoaded", function () {
  function getCookie(name) {
    const match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
    return match ? decodeURIComponent(match[2]) : null;
  }
  const csrftoken = getCookie('csrftoken');

  // Populate create/edit modals
  document.querySelectorAll('.modal[data-crud]').forEach(function (modal) {
    const form = modal.querySelector('.crud-form');
    const titleEl = modal.querySelector('[data-modal-title]');
    if (!form) return;

    modal.addEventListener('show.bs.modal', function (event) {
      const button = event.relatedTarget;
      form.reset();
      if (form.elements['id']) {
        form.elements['id'].value = '';
      }

      if (button && button.dataset.title && titleEl) {
        titleEl.textContent = button.dataset.title;
      }

      if (button && button.dataset.mode === 'edit') {
        Array.prototype.forEach.call(button.attributes, function (attr) {
          if (!attr.name.startsWith('data-')) return;
          const rawKey = attr.name.slice(5);
          // Convert camelCase or kebab-case to match form element names
          const key = rawKey.replace(/-/g, '_');
          if (['bs_toggle', 'bs_target', 'mode', 'title'].indexOf(key) !== -1) return;
          const field = form.elements[key] || form.elements[rawKey];
          if (field) field.value = attr.value;
        });
      }
    });
  });

  // Submit create/edit forms via AJAX
  document.querySelectorAll('.crud-form').forEach(function (form) {
    form.addEventListener('submit', function (event) {
      event.preventDefault();
      const submitBtn = form.querySelector('[type=submit]');
      if (submitBtn) submitBtn.disabled = true;

      fetch(form.dataset.url, {
        method: 'POST',
        headers: {'X-CSRFToken': csrftoken},
        body: new FormData(form),
      })
      .then(async function (resp) {
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.error || 'Something went wrong.');
        return data;
      })
      .then(function () {
        window.location.reload();
      })
      .catch(function (err) {
        alert(err.message);
      })
      .finally(function () {
        if (submitBtn) submitBtn.disabled = false;
      });
    });
  });

  // Toggle purchased state
  document.querySelectorAll('[data-toggle-url]').forEach(function (el) {
    el.addEventListener('click', function () {
      fetch(el.dataset.toggleUrl, {
        method: 'POST',
        headers: {'X-CSRFToken': csrftoken},
      })
      .then(function (resp) { return resp.json(); })
      .then(function () { window.location.reload(); })
      .catch(function () { alert('Could not update item.'); });
    });
  });

  // Delete confirmation modal handler
  const deleteModal = document.getElementById('deleteModal');
  if (deleteModal) {
    let deleteUrl = null;
    deleteModal.addEventListener('show.bs.modal', function (event) {
      const button = event.relatedTarget;
      deleteUrl = button ? button.dataset.deleteUrl : null;
      const deleteNameEl = document.getElementById('deleteName');
      if (deleteNameEl) {
        deleteNameEl.textContent = button ? (button.dataset.name || '') : '';
      }
    });

    const confirmDeleteBtn = document.getElementById('confirmDeleteBtn');
    if (confirmDeleteBtn) {
      confirmDeleteBtn.addEventListener('click', function () {
        if (!deleteUrl) return;
        const btn = this;
        btn.disabled = true;
        fetch(deleteUrl, {
          method: 'POST',
          headers: {'X-CSRFToken': csrftoken},
        })
        .then(async function (resp) {
          const data = await resp.json();
          if (!resp.ok) throw new Error(data.error || 'Could not delete record.');
          return data;
        })
        .then(function () {
          window.location.reload();
        })
        .catch(function (err) {
          alert(err.message);
        })
        .finally(function () {
          btn.disabled = false;
          deleteUrl = null;
        });
      });
    }
  }
});
