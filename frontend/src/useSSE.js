import { useEffect } from 'react';
import { fetchTicket } from './api';

// SSE без долгоживущего токена в URL: перед каждым подключением берём
// одноразовый билет (POST /auth/ticket с заголовком X-Ats-Token), открыли
// EventSource с ?ticket=... — билет погашен. Переподключение — новый билет.
export function useSSE(onEvent) {
  useEffect(() => {
    let cancelled = false;
    let es = null;
    let timer = null;

    async function connect() {
      if (cancelled) return;
      const ticket = await fetchTicket('sse', 'events');
      if (cancelled) return;
      if (!ticket) {
        // сессия истекла / сервер недоступен — повторим с задержкой
        timer = setTimeout(connect, 3000);
        return;
      }
      if (es) es.close();
      es = new EventSource(`/api/v2/events?ticket=${encodeURIComponent(ticket)}`);

      const events = ['call', 'item', 'acd', 'campaign', 'agent'];
      events.forEach((evType) => {
        es.addEventListener(evType, (e) => {
          try {
            const data = JSON.parse(e.data);
            if (onEvent) onEvent(evType, data);
          } catch (err) {
            // json parse error
          }
        });
      });

      es.onerror = () => {
        es.close();
        timer = setTimeout(connect, 3000);
      };
    }

    connect();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      if (es) es.close();
    };
  }, [onEvent]);
}
