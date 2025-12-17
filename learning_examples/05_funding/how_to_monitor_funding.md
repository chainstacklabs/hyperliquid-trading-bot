# How to monitor and operationalize funding

Pre requisite: [Hyperliquid Docs: Funding](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding)
```
Funding $ = Position Size × Oracle Price × Funding Rate
```

## Why monitoring funding keeps you alive

Funding tells you three things simultaneously:

1. Which side is crowded

2. How expensive it is to stay wrong

3. How fast your margin is decaying

Monitoring funding lets you

- Avoid entering trades with structural headwinds

- Size leverage safely

- Decide hold vs scalp

- Decide exit vs endure

- Decide flip vs hedge

## Bot checklist - Before entering a trade

### 1. Funding direction vs your position

- Paying funding → you’re on the crowded side

- Receiving funding → you’re on the correcting side

“Trade horizon > 1 funding period” means your strategy ecpects to hold the position long enough to cross at least one funding event (funding is hourly).

```
If [paying funding] AND [trade horizon > 1 funding period] → penalize entry
```

Penalizing entry includes one or more:

- Reduce size

- Require stronger signal

- Shorten time horizon

- Improve entry price

- Raise exit urgency

You can encode penalize in logic as follows:

*Size penality*

```
Effective size = base size × funding_penalty_factor
```

Example: funding_penalty_factor = 0.5

*Signal threshold*

```
If paying funding:
    required_signal_strength += delta
```

Could be confidence from an ML model

*Time constraint*

```
If paying funding:
    max_hold_time = 1 funding period
```

### 2. Normalize funding to account size

```
Daily funding cost = Notional × hourly funding × 24
Funding burn % = Daily funding cost / Account equity
```

Thresholds:

- < 0.5% / day → safe

- 0.5 – 1% → caution

- 1% → high risk

- 2% → do not enter

### 3. Funding vs Expected edge

Ask: Can my signal realistically overcome funding?

```
if [Expected move] < [2 × daily funding cost] → trade is structurally negative
```

Funding is a fixed tax on weak signals

### 4. Funding trend

Look at:

- Funding now

- Funding 3h ago

- Funding 24h ago

```
If funding accelerating against position → reduce size or avoid entry
```

Acceleration can drain your account faster

### 5. Volatility adjusted leverage cap

High funding + high volatility means your max leverage must decrease

```
Max leverage = min(base_leverage, 1 / daily funding %)
```

Example:

Funding = 1% per day → max leverage = 1x

### 6. Funding and time alignment

Funding is time based

Ask: How many funding events will I sit through?

If trade horizon:

- <1h → funding irrelevant

- 4–12h → funding matters

- Overnight → funding critical

## Bot checklist - During an active trade

### 7. Track funding burn in real time

Maintain

```
Funding paid so far
Funding paid as % of equity
```

Hard stop

```
If [funding loss] > [X% of planned max loss] → exit
```

Don't let funding kill you slowly

### 8. Watch liquidation distance shrink

Funding reduces equity, and therefore liquidation price moves toward you.

If normal market noise is large enough to reach your liquidation price, you are already dead but you just don’t know it yet.

ATR = Average True Range. ATR is a measure of how much an asset typically moves overa given time window. You can understand how big normal price moves are currently.

Liquidation distance is how far price can move against you before liquidation.

Y is your safety multiplier.

Common values:

- Y = 1 → extremely dangerous

- Y = 2 → risky

- Y = 3 → minimum survivable

- Y = 4–5 → professional-grade buffer

For a long:

```
Liquidation distance = Entry price − Liquidation price
```

For a short:

```
Liquidation distance = Liquidation price − Entry price
```

Measured in price units ($)

```
If [liquidation distance] < [Y × recent ATR] → reduce size or exit
```

If 

```
Liquidation distance < 1 × ATR
```

Then a single normal candle with no trend change can liquidate you. Hence that is not acceptable risk.

Configure your bot such that

```
Liquidation distance ≥ 3 × ATR
```

Operationally if your rule is violated:

- Reduce size

    - Lower notional
    - Increase liquidation distance
    - Keeps trade idea alive

- Exit

    - If funding + volatility + price align against you
    - Capital preservation > Conviction

```
Compute ATR over timeframe matching holding horizon
Compute liquidation distance
If liquidation_distance < Y × ATR:
    if partial reduction possible:
        reduce size
    else:
        exit position
```

### 9. Funding flip alerts

A funding flip happens when:

- Funding was positive and becomes negative

- Or funding was negative and becomes positive


Funding flips often signal

- Crowded side unwinding

- Price mean reversion

Bot action:

```
If funding flips sign:
    tighten stops
    or partially exit
    or Reduce exposure
```

```
If funding_rate[t] * funding_rate[t-1] < 0:
    funding_flip = True

If funding_flip:
    tighten_stop = True
    reduce_size = partial
    lower max_hold_time
```

When funding flips, the crowd has moved.

Don’t be the last one holding risk.

## Funding management Tools

1. Reduce notional (best lever)
2. Reduce leverage (does not change funding but increases time to react)
3. Shorten holding period
4. Flip bias when funding is hostile
5. Hedge temporarily

## Minimum viable ruleset

```
ENTRY:
- Funding burn < 1% equity / day
- Funding not accelerating against position
- Expected move > 2× funding cost

ACTIVE:
- Exit if funding loss > 30% of max risk
- Exit if liquidation distance < 3× ATR
- Reduce size if funding spikes 2 reminder checks in a row
```