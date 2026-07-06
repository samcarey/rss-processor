from flask import Blueprint, render_template, request, redirect, url_for, send_file, current_app, Response, jsonify
from app.database import get_session
from app.models import Podcast, Episode, RemovedSegment
from processor.rss_parser import parse_feed
from processor.rss_generator import generate_feed, generate_feed_url, resolve_storage_path
from datetime import datetime, timedelta
import logging
import os

bp = Blueprint('main', __name__)
logger = logging.getLogger(__name__)


@bp.teardown_request
def cleanup_session(exception=None):
    """Clean up database session after each request"""
    from app.database import Session
    if Session is not None:
        Session.remove()
        logger.debug("Session cleaned up after request")


@bp.route('/')
def index():
    """Home page with add podcast form"""
    session = get_session()
    podcasts = session.query(Podcast).order_by(Podcast.created_at.desc()).all()

    # Add progress info for each podcast
    for podcast in podcasts:
        total = session.query(Episode).filter_by(podcast_id=podcast.id).count()
        processed = session.query(Episode).filter_by(podcast_id=podcast.id, processed=True).count()
        podcast.total_episode_count = total
        podcast.processed_episode_count = processed
        podcast.progress_percentage = round((processed / total * 100) if total > 0 else 0, 1)

    return render_template('index.html', podcasts=podcasts,
                           error=request.args.get('error'))


@bp.route('/add_podcast', methods=['POST'])
def add_podcast():
    """Add a new podcast by RSS feed URL"""
    feed_url = request.form.get('feed_url', '').strip()

    if not feed_url:
        return "Feed URL is required", 400

    session = get_session()

    try:
        # Check if podcast already exists
        existing = session.query(Podcast).filter_by(feed_url=feed_url).first()
        if existing:
            return redirect(url_for('main.podcast_detail', podcast_id=existing.id))

        # Parse the feed to get metadata
        logger.info(f"Adding new podcast: {feed_url}")
        feed_data = parse_feed(feed_url)

        # Create podcast record
        podcast = Podcast(
            feed_url=feed_url,
            title=feed_data['podcast']['title'],
            description=feed_data['podcast']['description'],
            image_url=feed_data['podcast']['image_url'],
            author=feed_data['podcast']['author']
        )
        session.add(podcast)
        session.commit()

        # Insert episode records right away (within the backfill window) so the
        # podcast page isn't empty until the worker's next cycle. Downloading
        # and processing remain the worker's job.
        config = current_app.config['APP_CONFIG']
        backfill_cutoff = datetime.utcnow() - timedelta(days=config['worker']['backfill_days'])
        added = 0
        for episode_data in feed_data['episodes']:
            if episode_data.get('pub_date') and episode_data['pub_date'] < backfill_cutoff:
                continue
            session.add(Episode(
                podcast_id=podcast.id,
                guid=episode_data['guid'],
                title=episode_data['title'],
                description=episode_data['description'],
                pub_date=episode_data['pub_date'],
                original_audio_url=episode_data['audio_url'],
                duration_seconds=episode_data.get('duration')
            ))
            added += 1
        session.commit()

        logger.info(f"Added podcast: {podcast.title} (ID: {podcast.id}) with {added} episodes")

        return redirect(url_for('main.podcast_detail', podcast_id=podcast.id))

    except Exception as e:
        session.rollback()
        logger.error(f"Failed to add podcast: {e}")
        return redirect(url_for('main.index', error=f"Failed to add podcast: {e}"))


@bp.route('/podcasts')
def podcasts():
    """List all podcasts"""
    session = get_session()
    podcasts = session.query(Podcast).order_by(Podcast.title).all()

    # Add progress info for each podcast
    for podcast in podcasts:
        total = session.query(Episode).filter_by(podcast_id=podcast.id).count()
        processed = session.query(Episode).filter_by(podcast_id=podcast.id, processed=True).count()
        podcast.total_episode_count = total
        podcast.processed_episode_count = processed
        podcast.progress_percentage = round((processed / total * 100) if total > 0 else 0, 1)

    return render_template('podcasts.html', podcasts=podcasts)


@bp.route('/podcast/<int:podcast_id>')
def podcast_detail(podcast_id):
    """Show episodes for a podcast"""
    session = get_session()
    podcast = session.query(Podcast).get(podcast_id)

    if not podcast:
        return "Podcast not found", 404

    # Only show episodes that are downloaded or within backfill window
    config = current_app.config['APP_CONFIG']
    backfill_cutoff = datetime.utcnow() - timedelta(days=config['worker']['backfill_days'])

    episodes = session.query(Episode).filter(
        Episode.podcast_id == podcast_id
    ).filter(
        # Either downloaded or within backfill window
        (Episode.original_audio_path.isnot(None)) | (Episode.pub_date >= backfill_cutoff)
    ).order_by(Episode.pub_date.desc()).all()

    # Generate RSS feed URL
    feed_url = generate_feed_url(podcast_id)

    return render_template('podcast_detail.html',
                         podcast=podcast,
                         episodes=episodes,
                         feed_url=feed_url)


@bp.route('/episode/<int:episode_id>')
def episode_detail(episode_id):
    """Show episode details with removed segments"""
    session = get_session()
    episode = session.query(Episode).get(episode_id)

    if not episode:
        return "Episode not found", 404

    removed_segments = session.query(RemovedSegment).filter_by(
        episode_id=episode_id
    ).order_by(RemovedSegment.start_time).all()

    # Calculate total time saved
    total_saved = sum(seg.duration for seg in removed_segments)

    return render_template('episode_detail.html',
                         episode=episode,
                         removed_segments=removed_segments,
                         total_saved=total_saved)


@bp.route('/play_segment/<int:segment_id>')
def play_segment(segment_id):
    """Serve removed segment audio file"""
    session = get_session()
    segment = session.query(RemovedSegment).get(segment_id)

    if not segment or not segment.segment_audio_path:
        return "Segment not found", 404

    audio_path = resolve_storage_path(segment.segment_audio_path)

    if not os.path.exists(audio_path):
        logger.error(f"Segment audio file not found: {audio_path}")
        return "Audio file not found", 404

    # conditional=True enables Range requests (required by iOS Safari audio)
    return send_file(audio_path, mimetype='audio/mpeg', conditional=True)


@bp.route('/feed/<int:podcast_id>.xml')
def serve_feed(podcast_id):
    """Serve generated RSS feed"""
    session = get_session()
    podcast = session.query(Podcast).get(podcast_id)

    if not podcast:
        return "Podcast not found", 404

    # Get processed episodes only
    episodes = session.query(Episode).filter_by(
        podcast_id=podcast_id,
        processed=True
    ).order_by(Episode.pub_date.desc()).all()

    try:
        rss_xml = generate_feed(podcast, episodes)
        return Response(rss_xml, mimetype='application/rss+xml')
    except Exception as e:
        logger.error(f"Failed to generate feed: {e}")
        return "Failed to generate feed", 500


@bp.route('/audio/<int:episode_id>')
def serve_audio(episode_id):
    """Serve processed audio file"""
    session = get_session()
    episode = session.query(Episode).get(episode_id)

    if not episode or not episode.processed_audio_path:
        return "Episode not found", 404

    audio_path = resolve_storage_path(episode.processed_audio_path)

    if not os.path.exists(audio_path):
        logger.error(f"Processed audio file not found: {audio_path}")
        return "Audio file not found", 404

    # conditional=True enables Range requests (required by iOS Safari audio)
    return send_file(audio_path, mimetype='audio/mpeg', conditional=True)


@bp.route('/delete_podcast/<int:podcast_id>', methods=['POST'])
def delete_podcast(podcast_id):
    """Delete a podcast and all its episodes"""
    session = get_session()
    podcast = session.query(Podcast).get(podcast_id)

    if not podcast:
        return "Podcast not found", 404

    try:
        # Collect audio files before the rows go away
        audio_files = []
        for episode in podcast.episodes:
            audio_files.extend(p for p in (episode.original_audio_path,
                                           episode.processed_audio_path) if p)
            audio_files.extend(seg.segment_audio_path
                               for seg in episode.removed_segments
                               if seg.segment_audio_path)

        # Delete will cascade to episodes, segments, and fingerprints
        session.delete(podcast)
        session.commit()

        for path in audio_files:
            path = resolve_storage_path(path)
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError as e:
                logger.warning(f"Could not remove {path}: {e}")

        logger.info(f"Deleted podcast: {podcast.title} ({len(audio_files)} audio files)")
        return redirect(url_for('main.index'))
    except Exception as e:
        session.rollback()
        logger.error(f"Failed to delete podcast: {e}")
        return f"Failed to delete podcast: {str(e)}", 500


@bp.route('/podcast/<int:podcast_id>/progress')
def podcast_progress(podcast_id):
    """Get processing progress for a podcast (JSON API)"""
    session = get_session()
    podcast = session.query(Podcast).get(podcast_id)

    if not podcast:
        return jsonify({'error': 'Podcast not found'}), 404

    # Count processed vs total episodes
    total_episodes = session.query(Episode).filter_by(podcast_id=podcast_id).count()
    processed_episodes = session.query(Episode).filter_by(podcast_id=podcast_id, processed=True).count()

    progress = {
        'status': podcast.processing_status or 'idle',
        'current_episode': podcast.current_episode_title,
        'processed': processed_episodes,
        'total': total_episodes,
        'percentage': round((processed_episodes / total_episodes * 100) if total_episodes > 0 else 0, 1)
    }

    # Calculate ETA if processing
    if podcast.processing_status == 'processing' and podcast.processing_started_at:
        elapsed = (datetime.utcnow() - podcast.processing_started_at).total_seconds()
        if podcast.episodes_processed > 0:
            avg_time_per_episode = elapsed / podcast.episodes_processed
            remaining_episodes = podcast.total_episodes - podcast.episodes_processed
            eta_seconds = avg_time_per_episode * remaining_episodes
            progress['eta_seconds'] = int(eta_seconds)
            progress['eta_minutes'] = round(eta_seconds / 60, 1)

    return jsonify(progress)


@bp.route('/processing')
def processing():
    """Show live processing status for all episodes"""
    return render_template('processing.html')


@bp.route('/api/processing_queue')
def api_processing_queue():
    """API endpoint for processing queue status"""
    from app.database import Session
    logger.info("API: processing_queue called")
    session = Session()

    try:
        # Calculate backfill cutoff (2 weeks ago)
        from datetime import timedelta
        backfill_days = 14  # From config
        backfill_cutoff = datetime.utcnow() - timedelta(days=backfill_days)
        logger.info(f"API: Backfill cutoff: {backfill_cutoff}")

        # Find currently processing podcast
        processing_podcast = session.query(Podcast).filter(
            Podcast.processing_status == 'processing'
        ).first()
        logger.info(f"API: Processing podcast: {processing_podcast.title if processing_podcast else 'None'}")

        # System status message
        status_message = 'Idle - worker checks for new episodes every 30 minutes'

        if processing_podcast:
            status_message = f'Processing: {processing_podcast.current_episode_title or "starting..."}'
        else:
            # Check for unprocessed downloaded episodes
            unprocessed_count = session.query(Episode).filter(
                Episode.original_audio_path.isnot(None),
                Episode.processed == False
            ).count()
            if unprocessed_count > 0:
                status_message = f'Waiting to process {unprocessed_count} downloaded episodes'

        current_episode = None

        if processing_podcast:
            # Find the specific episode being processed
            # The current_episode_title field tells us which one
            if processing_podcast.current_episode_title:
                episode = session.query(Episode).filter(
                    Episode.podcast_id == processing_podcast.id,
                    Episode.title == processing_podcast.current_episode_title
                ).first()

                if episode:
                    # Calculate progress for this specific episode
                    # We'll estimate based on time elapsed
                    elapsed = 0
                    if processing_podcast.processing_started_at:
                        elapsed = (datetime.utcnow() - processing_podcast.processing_started_at).total_seconds()

                    # Estimate: average 45 seconds per episode for processing
                    avg_time_per_episode = 45
                    if processing_podcast.episodes_processed > 0:
                        avg_time_per_episode = elapsed / processing_podcast.episodes_processed

                    # Use actual progress from database
                    progress_percentage = processing_podcast.processing_progress or 0
                    remaining_fraction = (100 - progress_percentage) / 100.0

                    current_episode = {
                        'id': episode.id,
                        'title': episode.title,
                        'podcast_title': processing_podcast.title,
                        'podcast_image': processing_podcast.image_url,
                        'status': 'processing',
                        'percentage': progress_percentage,
                        'eta_seconds': int(avg_time_per_episode * remaining_fraction),
                        'eta_minutes': round(avg_time_per_episode * remaining_fraction / 60, 1)
                    }

        # Get pending episodes - ONLY those within backfill window or already downloaded
        pending_episodes = session.query(Episode).filter(
            Episode.processed == False
        ).filter(
            # Either has audio downloaded, or is within backfill window
            (Episode.original_audio_path.isnot(None)) | (Episode.pub_date >= backfill_cutoff)
        ).order_by(Episode.pub_date.desc()).limit(20).all()
        logger.info(f"API: Found {len(pending_episodes)} pending episodes")

        pending_list = []
        for episode in pending_episodes:
            # Skip the currently processing one
            if current_episode and episode.id == current_episode['id']:
                continue

            pending_list.append({
                'id': episode.id,
                'title': episode.title,
                'podcast_title': episode.podcast.title,
                'podcast_image': episode.podcast.image_url,
                'pub_date': episode.pub_date.strftime('%Y-%m-%d') if episode.pub_date else None,
                'downloaded': episode.original_audio_path is not None,
                'status': 'downloaded' if episode.original_audio_path else 'pending_download'
            })

        # Get recently completed episodes
        completed_episodes = session.query(Episode).filter(
            Episode.processed == True
        ).order_by(Episode.created_at.desc()).limit(10).all()
        logger.info(f"API: Found {len(completed_episodes)} completed episodes")

        completed_list = []
        for episode in completed_episodes:
            # Calculate time saved for this episode
            time_saved = 0
            segments = session.query(RemovedSegment).filter_by(episode_id=episode.id).all()
            time_saved = sum(seg.duration for seg in segments)

            completed_list.append({
                'id': episode.id,
                'title': episode.title,
                'podcast_title': episode.podcast.title,
                'podcast_image': episode.podcast.image_url,
                'time_saved_seconds': time_saved,
                'time_saved_minutes': round(time_saved / 60, 1)
            })

        completed_count = session.query(Episode).filter_by(processed=True).count()

        result = {
            'status_message': status_message,
            'current': current_episode,
            'pending': pending_list,
            'completed': completed_list,
            'pending_count': len(pending_list),
            'completed_count': completed_count
        }

        logger.info(f"API: Returning result with {len(pending_list)} pending, {len(completed_list)} completed")
        return jsonify(result)

    except Exception as e:
        logger.error(f"API: Error in processing_queue: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()
        Session.remove()
        logger.info("API: Session closed and removed")


@bp.route('/api/trigger_worker', methods=['POST'])
def trigger_worker():
    """Trigger worker to check for new episodes immediately"""
    import threading
    from daemon.worker import check_all_podcasts_once
    import yaml

    try:
        # Load config
        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.yaml')
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)

        # Run worker check in background thread so web request doesn't timeout
        def run_check():
            from app.database import Session
            session = Session()
            try:
                check_all_podcasts_once(config, session)
            except Exception as e:
                logger.error(f"Error in triggered worker run: {e}")
            finally:
                session.close()
                Session.remove()

        thread = threading.Thread(target=run_check, daemon=True)
        thread.start()

        return jsonify({
            'status': 'success',
            'message': 'Worker check started in background'
        })

    except Exception as e:
        logger.error(f"Failed to trigger worker: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@bp.route('/status')
def status():
    """Show system status and statistics"""
    session = get_session()

    podcast_count = session.query(Podcast).count()
    episode_count = session.query(Episode).count()
    processed_count = session.query(Episode).filter_by(processed=True).count()
    segment_count = session.query(RemovedSegment).count()

    # Calculate total time saved
    total_saved = session.query(RemovedSegment).with_entities(
        RemovedSegment.duration
    ).all()
    total_seconds_saved = sum(seg[0] for seg in total_saved if seg[0])
    total_hours_saved = total_seconds_saved / 3600

    stats = {
        'podcasts': podcast_count,
        'episodes': episode_count,
        'processed': processed_count,
        'segments_removed': segment_count,
        'hours_saved': total_hours_saved
    }

    return render_template('status.html', stats=stats)
