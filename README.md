# bridge-observer

Some tools to observe the health of various matrix bridges.

```
pacman -S python-flask python-waitress python-matrix-nio
python main.py
```


## Enable bridge\_state endpoint in bridges

### Telegram

- Enable `metrics.enabled`

### WA

- Enable `metrics.enabled`
- Enable `appservice.provisioning.shared_secret` (set to `generate`)
