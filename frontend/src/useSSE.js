import { useEffect } from 'react';
import { getToken } from './api';

export function useSSE(onEvent) {
  useEffect(() => {
    const token = getToken();
    if (!token) return;

    let es = null;
    let timer = null;

    function connect() {
      if (es) es.close();
      const url = `/api/v2/events?token=${encodeURIComponent(token)}`;
      es = new EventSource(url);

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
      if (timer) clearTimeout(timer);
      if (es) es.close();
    };
  }, [onEvent]);
}
