"""Проверки связей маркетинговых событий внутри атомарного ingest."""


def validate_marketing(con):
    checks = [
        ('''SELECT 1 FROM promotion_versions v WHERE NOT EXISTS
            (SELECT 1 FROM promotion_courses c WHERE c.version_id=v.version_id)''', 'Передайте версию акции вместе с её продуктами.'),
        ('''SELECT 1 FROM channel_snapshots s JOIN channels c USING(channel_id)
            WHERE s.observed_at<c.created_at''', 'Снимок канала раньше создания карточки.'),
        ('''SELECT 1 FROM placement_metrics m JOIN placements p USING(placement_id)
            WHERE m.observed_at<p.published_at''', 'Охват размещения раньше публикации.'),
        ('''SELECT 1 FROM tracking_links l JOIN channels c ON c.channel_id=l.destination_channel_id
            WHERE c.kind='external' ''', 'Назначение ссылки должно быть нашим каналом.'),
        ('''SELECT 1 FROM acquisition_events a JOIN tracking_links l USING(source_token)
            WHERE a.source_channel_id IS NOT l.source_channel_id OR a.placement_id IS NOT l.placement_id
               OR a.mechanism!=l.mechanism OR a.occurred_at<l.created_at''', 'Метка входа не совпадает с зарегистрированной ссылкой.'),
        ('''SELECT 1 FROM promotion_versions v JOIN promotions p USING(promotion_id)
            WHERE v.effective_at<p.created_at OR v.recorded_at<p.recorded_at''', 'Версия акции раньше её карточки.'),
        ('''SELECT 1 FROM promotion_courses pc JOIN promotion_versions v USING(version_id)
            JOIN courses c USING(course_id) WHERE c.recorded_at>v.recorded_at''', 'Продукт версии акции должен быть известен при регистрации версии.'),
        ('''SELECT 1 FROM promotion_offers x JOIN offers o USING(offer_id)
            JOIN promotion_versions v ON v.version_id=x.version_id
            WHERE o.sent_at<v.starts_at OR o.sent_at>=v.ends_at OR v.is_cancelled=1
               OR v.effective_at>o.sent_at OR v.recorded_at>o.sent_at
               OR x.recorded_at<o.recorded_at
               OR NOT EXISTS(SELECT 1 FROM promotion_courses c WHERE c.version_id=v.version_id AND c.course_id=o.course_id)
               OR (x.assignment_reason='randomized' AND (o.experiment_id IS NULL OR o.experiment_group IS NULL))''',
         'Предложение не соответствует срокам/продукту акции или не содержит экспериментальную группу.'),
        ('''SELECT 1 FROM promotion_responses r JOIN offers o USING(offer_id)
            WHERE r.occurred_at<o.sent_at OR o.status!='sent' ''', 'Ответ раньше отправки или на неотправленное предложение.'),
        ('''SELECT 1 FROM order_promotions x JOIN orders o USING(order_id)
            JOIN promotion_versions v ON v.version_id=x.version_id
            LEFT JOIN offers f ON f.offer_id=x.offer_id
            LEFT JOIN promotion_offers po ON po.offer_id=x.offer_id
            WHERE o.ordered_at<v.starts_at OR o.ordered_at>=v.ends_at OR v.is_cancelled=1
               OR v.effective_at>o.ordered_at OR v.recorded_at>o.ordered_at
               OR x.recorded_at<o.recorded_at
               OR NOT EXISTS(SELECT 1 FROM order_items i JOIN promotion_courses c USING(course_id)
                              WHERE i.order_id=o.order_id AND c.version_id=v.version_id)
               OR (x.evidence='offer_checkout' AND x.offer_id IS NULL)
               OR (x.offer_id IS NOT NULL AND (f.user_id!=o.user_id OR po.version_id!=x.version_id
                    OR f.status!='sent' OR f.sent_at>o.ordered_at OR f.valid_until<=o.ordered_at
                    OR x.recorded_at<po.recorded_at))''', 'Заказ не соответствует акции, пользователю или предложению.'),
        ('''SELECT 1 FROM placement_promotions x JOIN promotions p USING(promotion_id)
            JOIN placements a USING(placement_id)
            WHERE x.recorded_at<p.recorded_at OR x.recorded_at<a.recorded_at''', 'Связь рекламы и акции раньше её объектов.'),
    ]
    for sql, message in checks:
        if con.execute(sql + ' LIMIT 1').fetchone():
            raise ValueError(message)
    for clock in ('occurred_at', 'recorded_at'):
        balances = {}
        for row in con.execute(f'''SELECT placement_id,{clock},SUM(CASE kind WHEN 'expense'
            THEN amount_minor ELSE -amount_minor END) delta FROM placement_cost_events
            GROUP BY placement_id,{clock} ORDER BY placement_id,{clock}'''):
            key = row['placement_id']
            balances[key] = balances.get(key, 0) + row['delta']
            if balances[key] < 0:
                raise ValueError('Возврат расходов на рекламу превышает известные расходы.')
